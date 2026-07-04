from __future__ import annotations

import csv
import json
import os
from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys
import threading
import uuid
import zlib
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from struct import pack
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import ProxyHandler, build_opener, urlopen

from .metrics import machine_cpu_percent, system_cpu_times, system_memory


ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
FRONTEND_DIR = ROOT / "frontend"
CLIENT_OUTPUT_DIR = ROOT / "outputs" / "client"
LOAD_OUTPUT_DIR = ROOT / "outputs" / "load"
LOG_DIR = ROOT / "outputs" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
# Pastas onde cada servidor grava as imagens (PGM) assim que a reconstrucao termina.
SERVER_IMAGE_DIRS = {
    "python": ROOT / "outputs" / "python_server",
    "cpp": ROOT / "outputs" / "cpp_gateway",
}

RUNS: dict[str, dict[str, Any]] = {}
RUNS_LOCK = threading.Lock()

# Estado para calcular CPU% da maquina entre chamadas de /api/status.
_MACHINE_CPU_LOCK = threading.Lock()
_MACHINE_PREV_CPU: tuple[int, int] | None = None


def machine_snapshot() -> dict[str, Any]:
    """CPU% e RAM da MAQUINA inteira agora (para os cartoes do painel)."""
    global _MACHINE_PREV_CPU
    curr = system_cpu_times()
    with _MACHINE_CPU_LOCK:
        cpu = machine_cpu_percent(_MACHINE_PREV_CPU, curr)
        _MACHINE_PREV_CPU = curr
    used, total, load = system_memory()
    return {
        "cpu_percent": None if cpu is None else round(cpu, 1),
        "mem_used_mb": round(used / (1024 * 1024), 1),
        "mem_total_mb": round(total / (1024 * 1024), 1),
        "mem_load_percent": round(load, 1),
    }

# Servidores que o painel sabe ligar/desligar (linguagem interpretada e compilada).
SERVER_MODULES = {"python": "python.server", "cpp": "python.cpp_gateway_server"}
SERVER_PROCS: dict[str, dict[str, Any]] = {}
SERVER_LOCK = threading.Lock()


def as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def mean(values: list[float | None]) -> float | None:
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


# Opener SEM proxy: no Windows o urlopen padrao faz deteccao de proxy (WPAD/registro)
# a cada chamada, o que adicionava segundos ao /api/status. Isso evita esse custo.
_NO_PROXY_OPENER = build_opener(ProxyHandler({}))


def fetch_json(url: str, timeout: float = 1.0) -> dict[str, Any]:
    try:
        with _NO_PROXY_OPENER.open(url, timeout=timeout) as response:
            return {
                "online": True,
                "status_code": response.status,
                "data": json.loads(response.read().decode("utf-8")),
            }
    except Exception as exc:
        return {"online": False, "error": str(exc)}


def server_online(target: str, timeout: float = 0.6) -> bool:
    cfg = CONFIG["server"][target]
    return bool(fetch_json(f"http://{cfg['host']}:{cfg['port']}/status", timeout=timeout).get("online"))


def tail_text(path: Path, max_lines: int = 14) -> str:
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-max_lines:])


def start_server(target: str) -> dict[str, Any]:
    if target not in SERVER_MODULES:
        raise ValueError(f"servidor invalido: {target}")
    if server_online(target):
        return {"target": target, "status": "already_online"}

    with SERVER_LOCK:
        existing = SERVER_PROCS.get(target)
        if existing and existing["proc"].poll() is None:
            return {"target": target, "status": "starting", "pid": existing["proc"].pid}
        # Fecha handle de log de um processo anterior que ja morreu (evita vazamento).
        if existing:
            try:
                existing["log_file"].close()
            except OSError:
                pass

        log_path = LOG_DIR / f"{target}-server.log"
        log_file = log_path.open("w", encoding="utf-8")
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            proc = subprocess.Popen(
                [sys.executable, "-u", "-m", SERVER_MODULES[target]],
                cwd=ROOT,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        except Exception:
            log_file.close()
            raise
        SERVER_PROCS[target] = {
            "proc": proc,
            "log_file": log_file,
            "log_path": log_path,
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }
    return {"target": target, "status": "starting", "pid": proc.pid}


def kill_process_tree(proc: subprocess.Popen) -> None:
    """Encerra o processo e TODOS os seus filhos.

    No Windows, proc.terminate() mata apenas o processo direto; o gateway C++
    dispara `wsl.exe`/`cgnr_cpp` como netos, que ficariam orfaos. `taskkill /T`
    derruba a arvore inteira.
    """
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=6)
    except subprocess.TimeoutExpired:
        proc.kill()


def stop_server(target: str) -> dict[str, Any]:
    if target not in SERVER_MODULES:
        raise ValueError(f"servidor invalido: {target}")
    with SERVER_LOCK:
        entry = SERVER_PROCS.get(target)
    if not entry or entry["proc"].poll() is not None:
        # Mesmo sem processo vivo, garante o fechamento de um handle de log vazado.
        if entry:
            try:
                entry["log_file"].close()
            except OSError:
                pass
        return {
            "target": target,
            "status": "not_managed",
            "detail": "Este servidor nao foi iniciado pelo painel; nao ha processo para encerrar.",
        }

    proc = entry["proc"]
    kill_process_tree(proc)
    try:
        entry["log_file"].close()
    except OSError:
        pass
    return {"target": target, "status": "stopped", "pid": proc.pid}


def managed_state(target: str, online: bool | None = None) -> dict[str, Any]:
    with SERVER_LOCK:
        entry = SERVER_PROCS.get(target)
    running = bool(entry and entry["proc"].poll() is None)
    state: dict[str, Any] = {
        "managed": running,
        # Reaproveita o online ja obtido em dashboard_status (evita 2a chamada HTTP).
        "online": server_online(target) if online is None else online,
    }
    if entry:
        state["pid"] = entry["proc"].pid
        state["started_at"] = entry["started_at"]
        state["log_tail"] = tail_text(entry["log_path"])
        if not running:
            state["return_code"] = entry["proc"].poll()
    return state


def comparison_files() -> list[Path]:
    if not CLIENT_OUTPUT_DIR.exists():
        return []
    return sorted(CLIENT_OUTPUT_DIR.glob("comparison-*.csv"), key=lambda path: path.stat().st_mtime)


def load_window_files() -> list[Path]:
    if not LOAD_OUTPUT_DIR.exists():
        return []
    return sorted(LOAD_OUTPUT_DIR.glob("load-windows-*.csv"), key=lambda path: path.stat().st_mtime)


def load_result_files() -> list[Path]:
    if not LOAD_OUTPUT_DIR.exists():
        return []
    files = [
        path
        for path in LOAD_OUTPUT_DIR.glob("load-*.csv")
        if not path.name.startswith("load-windows-") and not path.name.startswith("load-resources-")
    ]
    return sorted(files, key=lambda path: path.stat().st_mtime)


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * percent
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    if lower == upper:
        return values[lower]
    weight = index - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def load_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for csv_path in comparison_files():
        generated_at = datetime.fromtimestamp(csv_path.stat().st_mtime).isoformat(timespec="seconds")
        with csv_path.open("r", newline="", encoding="utf-8") as file:
            for row in csv.DictReader(file):
                row["report_file"] = csv_path.name
                row["report_mtime"] = generated_at
                for key in (
                    "iterations",
                    "error_abs",
                    "residual_norm",
                    "lambda",
                    "reduction_factor_estimate",
                    "queue_ms",
                    "reconstruction_ms",
                    "roundtrip_ms",
                    "cpu_ms",
                    "rss_end_bytes",
                    "rss_end_kb",
                ):
                    row[key] = as_float(row.get(key))
                rows.append(row)
    return rows


def load_window_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for csv_path in load_window_files():
        generated_at = datetime.fromtimestamp(csv_path.stat().st_mtime).isoformat(timespec="seconds")
        with csv_path.open("r", newline="", encoding="utf-8") as file:
            for row in csv.DictReader(file):
                row["report_file"] = csv_path.name
                row["report_mtime"] = generated_at
                row["healthy"] = str(row.get("healthy", "")).lower() == "true"
                for key in (
                    "window",
                    "planned_rate_per_minute",
                    "next_rate_per_minute",
                    "target_rate_per_minute",
                    "best_stable_rate_per_minute",
                    "requests",
                    "total",
                    "ok",
                    "failed",
                    "error_rate",
                    "elapsed_seconds",
                    "achieved_requests_per_minute",
                    "max_planned_rate_per_minute",
                    "p50_roundtrip_ms",
                    "p95_roundtrip_ms",
                    "avg_reconstruction_ms",
                    "avg_queue_ms",
                ):
                    row[key] = as_float(row.get(key))
                rows.append(row)
    return rows


def aggregate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        server = str(row.get("server") or "unknown")
        model = str(row.get("model_id") or "unknown")
        groups.setdefault((server, model), []).append(row)

    aggregates: list[dict[str, Any]] = []
    for (server, model), items in sorted(groups.items()):
        ok_items = [row for row in items if row.get("status") == "ok"]
        reconstruction_ms = mean([row.get("reconstruction_ms") for row in ok_items])
        rss_values: list[float | None] = []
        for row in ok_items:
            rss_bytes = row.get("rss_end_bytes")
            rss_kb = row.get("rss_end_kb")
            if rss_bytes is not None:
                rss_values.append(rss_bytes)
            elif rss_kb is not None:
                rss_values.append(rss_kb * 1024.0)
        aggregates.append(
            {
                "server": server,
                "model_id": model,
                "runs": len(items),
                "ok_runs": len(ok_items),
                "failed_runs": len(items) - len(ok_items),
                "avg_iterations": mean([row.get("iterations") for row in ok_items]),
                "avg_error_abs": mean([row.get("error_abs") for row in ok_items]),
                "avg_queue_ms": mean([row.get("queue_ms") for row in ok_items]),
                "avg_reconstruction_ms": reconstruction_ms,
                "avg_roundtrip_ms": mean([row.get("roundtrip_ms") for row in ok_items]),
                "avg_cpu_ms": mean([row.get("cpu_ms") for row in ok_items]),
                "avg_rss_bytes": mean(rss_values),
                "images_per_second": (1000.0 / reconstruction_ms) if reconstruction_ms else None,
            }
        )
    return aggregates


def latest_load_execution() -> dict[str, Any] | None:
    files = load_result_files()
    if not files:
        return None
    csv_path = files[-1]
    generated_at = datetime.fromtimestamp(csv_path.stat().st_mtime).isoformat(timespec="seconds")
    rows: list[dict[str, Any]] = []
    with csv_path.open("r", newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            for key in (
                "planned_rate_per_minute",
                "roundtrip_ms",
                "reconstruction_ms",
                "queue_ms",
                "cpu_ms",
                "rss_end_bytes",
                "rss_end_kb",
            ):
                row[key] = as_float(row.get(key))
            rows.append(row)

    by_server: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_server.setdefault(str(row.get("server") or "unknown"), []).append(row)

    servers: list[dict[str, Any]] = []
    for server, items in sorted(by_server.items()):
        ok = [row for row in items if row.get("status") == "ok"]
        failed = [row for row in items if row.get("status") != "ok"]
        roundtrip_values = [value for value in (row.get("roundtrip_ms") for row in ok) if value is not None]
        reconstruction_values = [value for value in (row.get("reconstruction_ms") for row in ok) if value is not None]
        queue_values = [value for value in (row.get("queue_ms") for row in ok) if value is not None]
        cpu_values = [value for value in (row.get("cpu_ms") for row in ok) if value is not None]
        planned_rates = [value for value in (row.get("planned_rate_per_minute") for row in items) if value is not None]
        rss_values: list[float] = []
        for row in ok:
            rss_bytes = row.get("rss_end_bytes")
            rss_kb = row.get("rss_end_kb")
            if rss_bytes is not None:
                rss_values.append(rss_bytes)
            elif rss_kb is not None:
                rss_values.append(rss_kb * 1024.0)
        elapsed_ms = sum(roundtrip_values)
        servers.append(
            {
                "server": server,
                "total": len(items),
                "ok": len(ok),
                "failed": len(failed),
                "error_rate": (len(failed) / len(items)) if items else 0.0,
                "planned_rate_per_minute": max(planned_rates) if planned_rates else None,
                "achieved_requests_per_minute": (len(items) / (elapsed_ms / 1000.0) * 60.0) if elapsed_ms else None,
                "avg_reconstruction_ms": mean(reconstruction_values),
                "avg_roundtrip_ms": mean(roundtrip_values),
                "p95_roundtrip_ms": percentile(roundtrip_values, 0.95),
                "avg_queue_ms": mean(queue_values),
                "avg_cpu_ms": mean(cpu_values),
                "peak_rss_bytes": max(rss_values) if rss_values else None,
            }
        )

    return {
        "file": csv_path.name,
        "generated_at": generated_at,
        "total": len(rows),
        "servers": servers,
    }


def live_images(limit: int = 12) -> dict[str, Any]:
    """Imagens mais recentes lidas DIRETO das pastas de saida dos servidores.

    Diferente de `dashboard_summary`, nao espera o CSV comparativo (escrito so no
    fim do run): cada PGM aparece poucos segundos depois de a reconstrucao terminar,
    permitindo acompanhar as imagens surgindo ao vivo durante a execucao.
    """
    entries: list[dict[str, Any]] = []
    for server, directory in SERVER_IMAGE_DIRS.items():
        if not directory.exists():
            continue
        with os.scandir(directory) as it:
            for item in it:
                if not item.name.endswith(".pgm"):
                    continue
                try:
                    mtime = item.stat().st_mtime
                except OSError:
                    continue
                stem = item.name[:-4]
                model_id = stem.rsplit("-", 1)[-1] if "-" in stem else ""
                entries.append(
                    {
                        "server": server,
                        "model_id": model_id,
                        "image_path": str(directory / item.name),
                        "mtime": mtime,
                    }
                )
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    top = entries[:limit]
    for entry in top:
        entry["generated_at"] = datetime.fromtimestamp(entry.pop("mtime")).isoformat(timespec="seconds")
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(entries),
        "images": top,
    }


def dashboard_status() -> dict[str, Any]:
    python_cfg = CONFIG["server"]["python"]
    cpp_cfg = CONFIG["server"]["cpp"]
    with RUNS_LOCK:
        runs = sorted(RUNS.values(), key=lambda item: item["created_at"], reverse=True)
    # Busca o /status dos dois servidores EM PARALELO (nesta maquina, conectar numa
    # porta offline trava ate o timeout; em serie dava ~2s). Reaprovado no managed_state.
    urls = {
        "python": f"http://{python_cfg['host']}:{python_cfg['port']}/status",
        "cpp": f"http://{cpp_cfg['host']}:{cpp_cfg['port']}/status",
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {name: pool.submit(fetch_json, url, 0.8) for name, url in urls.items()}
        servers = {name: future.result() for name, future in futures.items()}
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "servers": servers,
        "managed": {target: managed_state(target, bool(servers[target].get("online"))) for target in SERVER_MODULES},
        "machine": machine_snapshot(),
        "dashboard_runs": runs[:8],
    }


def machine_by_server_from_last_load() -> dict[str, Any]:
    """CPU% e RAM da MAQUINA por servidor, lidos da ultima saturacao (um por vez).

    Como o teste roda um servidor de cada vez, o pico de CPU/RAM da maquina em
    cada fase reflete o custo real daquele servidor (inclui o binario C++ no WSL,
    que nao aparece no RSS do processo gateway).
    """
    if not LOAD_OUTPUT_DIR.exists():
        return {}
    files = sorted(LOAD_OUTPUT_DIR.glob("load-resources-*.csv"), key=lambda p: p.stat().st_mtime)
    if not files:
        return {}
    path = files[-1]
    per_server: dict[str, dict[str, list[float]]] = {}
    total_mb = None
    with path.open("r", newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            server = str(row.get("server") or "unknown")
            cpu = as_float(row.get("machine_cpu_percent"))
            mem = as_float(row.get("machine_mem_used_mb"))
            total_mb = as_float(row.get("machine_mem_total_mb")) or total_mb
            bucket = per_server.setdefault(server, {"cpu": [], "mem": []})
            if cpu is not None:
                bucket["cpu"].append(cpu)
            if mem is not None:
                bucket["mem"].append(mem)
    result: dict[str, Any] = {"file": path.name, "mem_total_mb": total_mb, "servers": {}}
    for server, data in per_server.items():
        result["servers"][server] = {
            "cpu_peak_percent": round(max(data["cpu"]), 1) if data["cpu"] else None,
            "cpu_avg_percent": round(mean(data["cpu"]), 1) if data["cpu"] else None,
            "mem_peak_mb": round(max(data["mem"]), 1) if data["mem"] else None,
            "mem_avg_mb": round(mean(data["mem"]), 1) if data["mem"] else None,
        }
    return result


def dashboard_summary() -> dict[str, Any]:
    rows = load_rows()
    load_windows = load_window_rows()
    image_rows = [row for row in rows if row.get("status") == "ok" and row.get("image_path")]
    reports = [
        {
            "file": path.name,
            "generated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            "bytes": path.stat().st_size,
        }
        for path in comparison_files()
    ]
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_rows": len(rows),
        "reports": reports[-10:],
        "aggregates": aggregate_rows(rows),
        "recent_rows": rows[-40:],
        "images": image_rows[-12:],
        "load_windows": load_windows[-30:],
        "latest_load": latest_load_execution(),
        "machine_by_server": machine_by_server_from_last_load(),
    }


def update_run(run_id: str, **fields: Any) -> None:
    with RUNS_LOCK:
        if run_id in RUNS:
            RUNS[run_id].update(fields)


def start_compare_run(payload: dict[str, Any]) -> dict[str, Any]:
    model = str(payload.get("model", "30x30"))
    gain = str(payload.get("gain", "scalar"))
    count = int(payload.get("count", 1))
    if model not in CONFIG["models"]:
        raise ValueError(f"modelo invalido: {model}")
    if gain not in {"none", "scalar", "formula"}:
        raise ValueError(f"ganho invalido: {gain}")
    if count < 1 or count > 20:
        raise ValueError("count deve ficar entre 1 e 20")

    run_id = str(uuid.uuid4())
    run_state = {
        "id": run_id,
        "type": "compare",
        "status": "running",
        "model": model,
        "gain": gain,
        "count": count,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": None,
        "return_code": None,
        "stdout": "",
        "stderr": "",
        "log": [],
        "last_line": "",
    }
    with RUNS_LOCK:
        RUNS[run_id] = run_state

    thread = threading.Thread(target=run_compare_process, args=(run_id, model, count, gain), daemon=True)
    thread.start()
    return run_state


def start_load_run(payload: dict[str, Any]) -> dict[str, Any]:
    server = str(payload.get("server", "both"))
    model = str(payload.get("model", "30x30"))
    clients = int(payload.get("clients", 3))
    rate = float(payload.get("rate_per_minute", 200))
    requests = int(payload.get("requests", 200))
    gain = str(payload.get("gain", "none"))
    mode = str(payload.get("mode", "fixed"))

    if server not in {"python", "cpp", "both"}:
        raise ValueError(f"servidor invalido: {server}")
    if model not in CONFIG["models"]:
        raise ValueError(f"modelo invalido: {model}")
    if gain not in {"none", "scalar", "formula"}:
        raise ValueError(f"ganho invalido: {gain}")
    if mode not in {"fixed", "adaptive"}:
        raise ValueError(f"modo invalido: {mode}")
    if clients < 1 or clients > 60:
        raise ValueError("clients deve ficar entre 1 e 60")
    if requests < 1 or requests > 1000:
        raise ValueError("requests deve ficar entre 1 e 1000")
    if rate <= 0 or rate > 1000:
        raise ValueError("rate_per_minute deve ficar entre 1 e 1000")

    run_id = str(uuid.uuid4())
    run_state = {
        "id": run_id,
        "type": "load",
        "status": "running",
        "server": server,
        "model": model,
        "clients": clients,
        "rate_per_minute": rate,
        "requests": requests,
        "gain": gain,
        "mode": mode,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": None,
        "return_code": None,
        "stdout": "",
        "stderr": "",
        "log": [],
        "last_line": "",
    }
    with RUNS_LOCK:
        RUNS[run_id] = run_state

    thread = threading.Thread(
        target=run_load_process,
        args=(run_id, server, model, mode, clients, rate, requests, gain),
        daemon=True,
    )
    thread.start()
    return run_state


def append_log_line(run_id: str, line: str) -> None:
    """Acrescenta uma linha ao log ao vivo da execucao (visivel no painel)."""
    with RUNS_LOCK:
        run = RUNS.get(run_id)
        if run is None:
            return
        log = run.setdefault("log", [])
        log.append(line)
        # Mantem apenas as ultimas linhas para nao crescer sem limite.
        if len(log) > 400:
            del log[: len(log) - 400]
        run["last_line"] = line


def stream_run(run_id: str, command: list[str], timeout: float) -> None:
    """Executa o comando transmitindo cada linha de saida em tempo real.

    Isso permite acompanhar a execucao "envio a envio" no painel, em vez de so
    ver o resultado quando o processo termina.
    """
    append_log_line(run_id, f"$ {' '.join(command[1:])}")
    timed_out = {"hit": False}
    try:
        with subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        ) as proc:
            # Watchdog: o loop de leitura so retorna quando o filho fecha stdout.
            # Se o processo travar sem terminar, este timer mata a arvore no prazo.
            def _deadline() -> None:
                timed_out["hit"] = True
                append_log_line(run_id, f"[timeout] processo excedeu {int(timeout)}s; encerrando.")
                kill_process_tree(proc)

            watchdog = threading.Timer(timeout, _deadline)
            watchdog.daemon = True
            watchdog.start()
            try:
                assert proc.stdout is not None
                for raw_line in proc.stdout:
                    append_log_line(run_id, raw_line.rstrip("\n"))
                proc.wait()
            finally:
                watchdog.cancel()

        with RUNS_LOCK:
            tail = "\n".join(RUNS.get(run_id, {}).get("log", [])[-80:])
        status = "failed" if (timed_out["hit"] or proc.returncode != 0) else "completed"
        update_run(
            run_id,
            status=status,
            finished_at=datetime.now().isoformat(timespec="seconds"),
            return_code=proc.returncode,
            stdout=tail,
        )
    except Exception as exc:
        append_log_line(run_id, f"[erro] {exc}")
        update_run(
            run_id,
            status="failed",
            finished_at=datetime.now().isoformat(timespec="seconds"),
            return_code=-1,
            stderr=str(exc),
        )


def run_compare_process(run_id: str, model: str, count: int, gain: str) -> None:
    # "-u" garante saida sem buffer, para o log aparecer linha a linha no painel.
    command = [
        sys.executable,
        "-u",
        str(ROOT / "client" / "compare_client.py"),
        "--model",
        model,
        "--count",
        str(count),
        "--gain",
        gain,
    ]
    stream_run(run_id, command, timeout=float(CONFIG["server"]["request_timeout_seconds"]) + 60.0)


def run_load_process(
    run_id: str,
    server: str,
    model: str,
    mode: str,
    clients: int,
    rate: float,
    requests: int,
    gain: str,
) -> None:
    command = [
        sys.executable,
        "-u",
        str(ROOT / "scripts" / "load_test.py"),
        "--server",
        server,
        "--model",
        model,
        "--mode",
        mode,
        "--clients",
        str(clients),
        "--rate-per-minute",
        str(rate),
        "--requests",
        str(requests),
        "--gain",
        gain,
    ]
    stream_run(run_id, command, timeout=float(CONFIG["server"]["request_timeout_seconds"]) * 4.0)


def resolve_image_path(raw_path: str) -> Path | None:
    decoded = unquote(raw_path)
    if not decoded:
        return None
    candidates: list[Path] = []
    original = Path(decoded)
    if original.is_absolute():
        candidates.append(original)
    candidates.append((ROOT / decoded).resolve())
    if original.name:
        candidates.extend((ROOT / "outputs").rglob(original.name))

    root_resolved = ROOT.resolve()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root_resolved)
        except (OSError, ValueError):
            continue
        if resolved.suffix.lower() == ".pgm" and resolved.exists():
            return resolved
    return None


def read_pgm(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    index = 0

    def read_token() -> bytes:
        nonlocal index
        while index < len(data):
            byte = data[index]
            if byte in b" \t\r\n":
                index += 1
                continue
            if byte == ord("#"):
                while index < len(data) and data[index] not in b"\r\n":
                    index += 1
                continue
            break
        start = index
        while index < len(data) and data[index] not in b" \t\r\n":
            index += 1
        return data[start:index]

    if read_token() != b"P5":
        raise ValueError("imagem PGM precisa estar em formato P5")
    width = int(read_token())
    height = int(read_token())
    max_value = int(read_token())
    while index < len(data) and data[index] in b" \t\r\n":
        index += 1
    pixels = data[index:]
    if max_value > 255:
        pixels = pixels[1::2]
    expected = width * height
    if len(pixels) < expected:
        raise ValueError("arquivo PGM incompleto")
    return width, height, pixels[:expected]


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind)
    crc = zlib.crc32(payload, crc)
    return pack(">I", len(payload)) + kind + payload + pack(">I", crc & 0xFFFFFFFF)


def pgm_to_png(path: Path) -> bytes:
    width, height, pixels = read_pgm(path)
    rows = [pixels[index : index + width] for index in range(0, len(pixels), width)]
    filtered = b"".join(b"\x00" + row for row in rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(filtered, level=6))
        + png_chunk(b"IEND", b"")
    )


def content_type(path: Path) -> str:
    if path.suffix.lower() == ".html":
        return "text/html; charset=utf-8"
    if path.suffix.lower() == ".css":
        return "text/css; charset=utf-8"
    if path.suffix.lower() == ".js":
        return "application/javascript; charset=utf-8"
    return "application/octet-stream"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/status":
                self.send_json(200, dashboard_status())
                return
            if parsed.path == "/api/summary":
                self.send_json(200, dashboard_summary())
                return
            if parsed.path == "/api/live-images":
                self.send_json(200, live_images())
                return
            if parsed.path == "/api/runs":
                with RUNS_LOCK:
                    runs = sorted(RUNS.values(), key=lambda item: item["created_at"], reverse=True)
                self.send_json(200, {"runs": runs})
                return
            if parsed.path == "/api/image":
                raw_path = parse_qs(parsed.query).get("path", [""])[0]
                image_path = resolve_image_path(raw_path)
                if image_path is None:
                    self.send_json(404, {"error": "imagem nao encontrada"})
                    return
                self.send_binary(200, "image/png", pgm_to_png(image_path), cache_seconds=86400)
                return
            self.serve_static(parsed.path)
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in {"/api/run", "/api/load-run", "/api/server-control"}:
            self.send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if parsed.path == "/api/load-run":
                self.send_json(202, start_load_run(payload))
            elif parsed.path == "/api/server-control":
                action = str(payload.get("action", ""))
                target = str(payload.get("target", ""))
                if action == "start":
                    self.send_json(202, start_server(target))
                elif action == "stop":
                    self.send_json(202, stop_server(target))
                else:
                    self.send_json(400, {"error": f"acao invalida: {action}"})
            else:
                self.send_json(202, start_compare_run(payload))
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

    def serve_static(self, route: str) -> None:
        if route in {"", "/"}:
            path = FRONTEND_DIR / "index.html"
        else:
            relative = route.lstrip("/")
            if ".." in Path(relative).parts:
                self.send_json(400, {"error": "invalid path"})
                return
            path = FRONTEND_DIR / relative
        if not path.exists() or not path.is_file():
            self.send_json(404, {"error": "not found"})
            return
        self.send_binary(200, content_type(path), path.read_bytes())

    def send_json(self, status: int, data: dict[str, Any]) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_binary(status, "application/json; charset=utf-8", body)

    def send_binary(self, status: int, mime_type: str, body: bytes, cache_seconds: int = 0) -> None:
        self.send_response(status)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(body)))
        if cache_seconds > 0:
            # Imagens sao imutaveis (nome = uuid): o navegador pode cachear e
            # nao re-baixar/re-converter a cada atualizacao do painel.
            self.send_header("Cache-Control", f"public, max-age={cache_seconds}, immutable")
        else:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def main() -> None:
    host = CONFIG["server"]["dashboard"]["host"]
    port = int(CONFIG["server"]["dashboard"]["port"])
    print(f"Dashboard CGNR listening on http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
