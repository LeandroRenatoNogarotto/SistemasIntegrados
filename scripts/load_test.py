from __future__ import annotations

import argparse
import csv
import ctypes
import json
import math
import os
import random
import statistics
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
LOAD_CONFIG = CONFIG["load_test"]
OUTPUT_DIR = ROOT / "outputs" / "load"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR = ROOT / "outputs" / "logs"
# Modulos dos servidores que o teste sabe ligar/desligar (um por vez).
SERVER_MODULES = {"python": "python.server", "cpp": "python.cpp_gateway_server"}


# --------------------------------------------------------------------------
# Metricas da MAQUINA inteira (nao so do processo do servidor).
# Feitas com a API do Windows via ctypes, sem depender de psutil.
# --------------------------------------------------------------------------
def system_memory() -> tuple[int, int, float]:
    """(usada_bytes, total_bytes, load_percent) da RAM fisica da maquina."""
    if os.name == "nt":
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        total = int(status.ullTotalPhys)
        used = total - int(status.ullAvailPhys)
        return used, total, float(status.dwMemoryLoad)
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                if rest:
                    info[key.strip()] = int(rest.strip().split()[0]) * 1024
        total = info.get("MemTotal", 0)
        used = total - info.get("MemAvailable", 0)
        load = (used / total * 100.0) if total else 0.0
        return used, total, load
    except Exception:
        return 0, 0, 0.0


def system_cpu_times() -> tuple[int, int]:
    """(idle_ticks, total_ticks) da CPU da maquina; use deltas para CPU%."""
    if os.name == "nt":
        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))

        def as_int(value: wintypes.FILETIME) -> int:
            return (value.dwHighDateTime << 32) | value.dwLowDateTime

        # No Windows, "kernel" ja inclui o idle; total = kernel + user.
        return as_int(idle), as_int(kernel) + as_int(user)
    try:
        with open("/proc/stat", encoding="utf-8") as handle:
            fields = [int(v) for v in handle.readline().split()[1:]]
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        return idle, sum(fields)
    except Exception:
        return 0, 0


def machine_cpu_percent(prev: tuple[int, int] | None, curr: tuple[int, int]) -> float | None:
    """CPU% da maquina entre duas leituras de system_cpu_times()."""
    if prev is None:
        return None
    idle_delta = curr[0] - prev[0]
    total_delta = curr[1] - prev[1]
    if total_delta <= 0:
        return None
    busy = total_delta - idle_delta
    return max(0.0, min(100.0, busy / total_delta * 100.0))


def resolve_project_path(path: str) -> Path:
    return (ROOT / path).resolve()


def read_vector(path: Path) -> np.ndarray:
    return np.loadtxt(path, dtype=np.float64)


def apply_gain(signal: np.ndarray, mode: str) -> tuple[np.ndarray, str]:
    if mode == "none":
        return signal.copy(), "none"
    if mode == "scalar":
        gain = random.uniform(0.85, 1.15)
        return signal * gain, f"scalar:{gain:.8f}"
    if mode == "formula":
        indexes = np.arange(1, signal.shape[0] + 1, dtype=np.float64)
        gamma = np.sqrt(100.0 + 0.05 * indexes * indexes)
        return signal * gamma, "formula:sqrt(100+0.05*l^2)"
    raise ValueError(f"unknown gain mode {mode}")


def worker_count(value: object) -> int:
    """Resolve max_workers que pode vir como numero ou "auto" no config."""
    cores = os.cpu_count() or 1
    if value in (None, "auto", "", 0, "0"):
        return max(2, cores - 1)
    try:
        requested = int(value)
    except (TypeError, ValueError):
        return max(2, cores - 1)
    if requested <= 0:
        return max(2, cores - 1)
    return max(1, min(cores, requested))


def resolve_clients(value: str) -> int:
    if value != "auto":
        clients = int(value)
        if clients < 1:
            raise ValueError("--clients precisa ser maior que zero")
        return clients

    server_workers = [
        worker_count(CONFIG["server"]["python"]["max_workers"]),
        worker_count(CONFIG["server"]["cpp"]["max_workers"]),
    ]
    default_clients = int(LOAD_CONFIG["default_clients"])
    queue_limit = int(CONFIG["server"]["queue_limit"])
    return max(3, default_clients, min(queue_limit, max(server_workers) + 1))


def cpp_base_url() -> str:
    host = CONFIG["server"]["cpp"]["host"]
    port = CONFIG["server"]["cpp"]["port"]
    if host == "wsl-auto":
        completed = subprocess.run(
            ["wsl", "-d", "Codex-Debian", "--exec", "hostname", "-I"],
            check=True,
            capture_output=True,
            text=True,
        )
        host = completed.stdout.strip().split()[0]
    return f"http://{host}:{port}"


def status_url(server: str) -> str:
    if server == "python":
        return f"http://{CONFIG['server']['python']['host']}:{CONFIG['server']['python']['port']}/status"
    if server == "cpp":
        return f"{cpp_base_url()}/status"
    raise ValueError(server)


def fetch_status(server: str, timeout: float = 5.0) -> dict | None:
    try:
        with urlopen(status_url(server), timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


class ResourceSampler(threading.Thread):
    """Amostra CPU e memoria dos servidores periodicamente via /status.

    Atende a exigencia da Atividade 4 de "medir memoria e CPU" durante o teste
    de saturacao. CPU% e calculada pelo delta de cpu_process_ms entre amostras
    (sem depender de psutil); RSS, fila e jobs ativos sao registrados ao longo
    de todo o teste, gerando uma serie temporal.
    """

    def __init__(self, targets: list[str], interval: float = 0.5) -> None:
        super().__init__(name="resource-sampler", daemon=True)
        self.targets = targets
        self.interval = interval
        self.samples: list[dict] = []
        # Serie da MAQUINA inteira (uma amostra por tick, independente do servidor).
        self.machine_samples: list[dict] = []
        self._stop_event = threading.Event()
        self._prev: dict[str, tuple[float, float]] = {}
        self._prev_cpu_times: tuple[int, int] | None = None

    def run(self) -> None:
        while not self._stop_event.is_set():
            now = time.perf_counter()
            stamp = datetime.now().isoformat(timespec="milliseconds")

            # Uma leitura da maquina por tick (CPU% via delta; RAM instantanea).
            cpu_times = system_cpu_times()
            mcpu = machine_cpu_percent(self._prev_cpu_times, cpu_times)
            self._prev_cpu_times = cpu_times
            mem_used, mem_total, mem_load = system_memory()
            machine = {
                "machine_cpu_percent": None if mcpu is None else round(mcpu, 2),
                "machine_mem_used_mb": round(mem_used / (1024 * 1024), 1),
                "machine_mem_total_mb": round(mem_total / (1024 * 1024), 1),
                "machine_mem_load_percent": round(mem_load, 1),
            }
            self.machine_samples.append({"timestamp": stamp, **machine})

            for server in self.targets:
                status = fetch_status(server)
                if not status:
                    continue
                cpu_ms = as_float(status.get("cpu_process_ms"))
                cpu_percent = None
                prev = self._prev.get(server)
                if prev is not None and cpu_ms is not None:
                    prev_wall, prev_cpu = prev
                    wall_delta = now - prev_wall
                    if wall_delta > 0:
                        cpu_percent = max(0.0, (cpu_ms - prev_cpu) / (wall_delta * 1000.0) * 100.0)
                if cpu_ms is not None:
                    self._prev[server] = (now, cpu_ms)
                self.samples.append(
                    {
                        "timestamp": stamp,
                        "server": server,
                        "queue_size": status.get("queue_size"),
                        "active_jobs": status.get("active_jobs"),
                        "completed_jobs": status.get("completed_jobs"),
                        "rejected_jobs": status.get("rejected_jobs"),
                        "rss_bytes": status.get("rss_bytes"),
                        "rss_mb": round((as_float(status.get("rss_bytes")) or 0.0) / (1024 * 1024), 3),
                        "cpu_process_ms": cpu_ms,
                        "cpu_percent": None if cpu_percent is None else round(cpu_percent, 2),
                        # Estado da maquina inteira no mesmo instante:
                        **machine,
                    }
                )
            self._stop_event.wait(self.interval)

    def stop(self) -> None:
        self._stop_event.set()

    def machine_summary(self) -> dict[str, object]:
        cpu = [s["machine_cpu_percent"] for s in self.machine_samples if s["machine_cpu_percent"] is not None]
        mem_used = [s["machine_mem_used_mb"] for s in self.machine_samples if s["machine_mem_used_mb"] is not None]
        load = [s["machine_mem_load_percent"] for s in self.machine_samples if s["machine_mem_load_percent"] is not None]
        total = next((s["machine_mem_total_mb"] for s in self.machine_samples if s.get("machine_mem_total_mb")), None)
        return {
            "samples": len(self.machine_samples),
            "cpu_peak_percent": round(max(cpu), 2) if cpu else None,
            "cpu_avg_percent": round(statistics.fmean(cpu), 2) if cpu else None,
            "mem_peak_used_mb": round(max(mem_used), 1) if mem_used else None,
            "mem_avg_used_mb": round(statistics.fmean(mem_used), 1) if mem_used else None,
            "mem_total_mb": total,
            "mem_load_peak_percent": round(max(load), 1) if load else None,
        }

    def summary(self) -> dict[str, object]:
        result: dict[str, object] = {}
        for server in self.targets:
            rss = [as_float(s["rss_bytes"]) for s in self.samples if s["server"] == server]
            rss = [v for v in rss if v is not None]
            cpu = [s["cpu_percent"] for s in self.samples if s["server"] == server and s["cpu_percent"] is not None]
            queue_depth = [as_float(s["queue_size"]) for s in self.samples if s["server"] == server]
            queue_depth = [v for v in queue_depth if v is not None]
            result[server] = {
                "samples": sum(1 for s in self.samples if s["server"] == server),
                "rss_peak_mb": round(max(rss) / (1024 * 1024), 2) if rss else None,
                "rss_avg_mb": round(statistics.fmean(rss) / (1024 * 1024), 2) if rss else None,
                "cpu_peak_percent": round(max(cpu), 2) if cpu else None,
                "cpu_avg_percent": round(statistics.fmean(cpu), 2) if cpu else None,
                "queue_peak": int(max(queue_depth)) if queue_depth else None,
            }
        return result


def post_python(model_id: str, signal: np.ndarray, timeout: float) -> dict:
    url = f"http://{CONFIG['server']['python']['host']}:{CONFIG['server']['python']['port']}/reconstruct"
    body = json.dumps({"model_id": model_id, "g": signal.tolist()}).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_cpp(model_id: str, signal: np.ndarray, timeout: float) -> dict:
    url = f"{cpp_base_url()}/reconstruct"
    job_id = str(uuid.uuid4())
    body = np.asarray(signal, dtype=np.float64).tobytes()
    request = Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/octet-stream",
            "X-Model-Id": model_id,
            "X-Job-Id": job_id,
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def empty_error_row(
    client_id: int,
    sequence: int,
    window: int,
    planned_rate: float,
    server: str,
    model_id: str,
    signal_file: str,
    started_at: str,
    elapsed_ms: float,
    status: str,
    http_status: int | str,
    error: str,
) -> dict:
    return {
        "client_id": client_id,
        "sequence": sequence,
        "window": window,
        "planned_rate_per_minute": planned_rate,
        "server": server,
        "model_id": model_id,
        "signal_file": signal_file,
        "status": status,
        "http_status": http_status,
        "started_at": started_at,
        "roundtrip_ms": elapsed_ms,
        "reconstruction_ms": "",
        "queue_ms": "",
        "iterations": "",
        "error_abs": "",
        "cpu_ms": "",
        "rss_end_bytes": "",
        "rss_end_kb": "",
        "error": error[:500],
    }


def call_server(
    client_id: int,
    sequence: int,
    window: int,
    planned_rate: float,
    server: str,
    model_id: str,
    signal: np.ndarray,
    signal_file: str,
) -> dict:
    started = time.perf_counter()
    started_at = datetime.now().isoformat(timespec="milliseconds")
    timeout = float(CONFIG["server"]["request_timeout_seconds"]) + 30.0
    try:
        if server == "python":
            response = post_python(model_id, signal, timeout)
        elif server == "cpp":
            response = post_cpp(model_id, signal, timeout)
        else:
            raise ValueError(server)
        return {
            "client_id": client_id,
            "sequence": sequence,
            "window": window,
            "planned_rate_per_minute": planned_rate,
            "server": server,
            "model_id": model_id,
            "signal_file": signal_file,
            "status": "ok",
            "http_status": 200,
            "started_at": started_at,
            "roundtrip_ms": (time.perf_counter() - started) * 1000.0,
            "reconstruction_ms": response.get("reconstruction_ms"),
            "queue_ms": response.get("queue_ms"),
            "iterations": response.get("iterations"),
            "error_abs": response.get("error_abs"),
            "cpu_ms": response.get("cpu_ms"),
            "rss_end_bytes": response.get("rss_end_bytes"),
            "rss_end_kb": response.get("rss_end_kb"),
            "error": "",
        }
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return empty_error_row(
            client_id,
            sequence,
            window,
            planned_rate,
            server,
            model_id,
            signal_file,
            started_at,
            (time.perf_counter() - started) * 1000.0,
            "http_error",
            exc.code,
            body,
        )
    except (TimeoutError, URLError, OSError, ValueError) as exc:
        return empty_error_row(
            client_id,
            sequence,
            window,
            planned_rate,
            server,
            model_id,
            signal_file,
            started_at,
            (time.perf_counter() - started) * 1000.0,
            "connection_error",
            "",
            str(exc),
        )


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


def as_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summarize(rows: list[dict], elapsed_seconds: float) -> dict[str, object]:
    ok = [row for row in rows if row["status"] == "ok"]
    failed = [row for row in rows if row["status"] != "ok"]
    roundtrips = [value for value in (as_float(row["roundtrip_ms"]) for row in ok) if value is not None]
    reconstructions = [value for value in (as_float(row["reconstruction_ms"]) for row in ok) if value is not None]
    queues = [value for value in (as_float(row["queue_ms"]) for row in ok) if value is not None]
    cpus = [value for value in (as_float(row.get("cpu_ms")) for row in ok) if value is not None]
    rss_values = []
    for row in ok:
        rss_b = as_float(row.get("rss_end_bytes"))
        if rss_b is None:
            rss_kb = as_float(row.get("rss_end_kb"))
            rss_b = rss_kb * 1024.0 if rss_kb is not None else None
        if rss_b is not None:
            rss_values.append(rss_b)
    planned_rates = [value for value in (as_float(row["planned_rate_per_minute"]) for row in rows) if value is not None]
    return {
        "total": len(rows),
        "ok": len(ok),
        "failed": len(failed),
        "error_rate": (len(failed) / len(rows)) if rows else 0.0,
        "elapsed_seconds": elapsed_seconds,
        "achieved_requests_per_minute": (len(rows) / elapsed_seconds * 60.0) if elapsed_seconds else 0.0,
        "max_planned_rate_per_minute": max(planned_rates) if planned_rates else None,
        "p50_roundtrip_ms": percentile(roundtrips, 0.50),
        "p95_roundtrip_ms": percentile(roundtrips, 0.95),
        "avg_reconstruction_ms": statistics.fmean(reconstructions) if reconstructions else None,
        "avg_queue_ms": statistics.fmean(queues) if queues else None,
        "avg_cpu_ms": statistics.fmean(cpus) if cpus else None,
        "p95_cpu_ms": percentile(cpus, 0.95),
        "peak_rss_mb": (max(rss_values) / (1024 * 1024)) if rss_values else None,
    }


def window_summary(rows: list[dict]) -> dict[str, object]:
    elapsed = 0.0
    if rows:
        started_values = [row["started_at"] for row in rows]
        elapsed = sum(float(row["roundtrip_ms"]) for row in rows) / 1000.0
        _ = started_values
    summary = summarize(rows, elapsed)
    summary["requests"] = len(rows)
    return summary


def is_healthy(summary: dict[str, object], args: argparse.Namespace) -> tuple[bool, str]:
    error_rate = float(summary["error_rate"])
    p95 = summary["p95_roundtrip_ms"]
    avg_queue = summary["avg_queue_ms"]
    if error_rate > args.max_error_rate:
        return False, f"taxa de erro {error_rate:.2%} acima do limite"
    if p95 is not None and float(p95) > args.max_p95_roundtrip_ms:
        return False, f"p95 {float(p95):.1f} ms acima do limite"
    if avg_queue is not None and float(avg_queue) > args.max_avg_queue_ms:
        return False, f"fila media {float(avg_queue):.1f} ms acima do limite"
    return True, "saudavel"


def run_window(
    pool: ThreadPoolExecutor,
    server: str,
    model: str,
    signal: np.ndarray,
    signal_file: str,
    clients: int,
    window: int,
    requests: int,
    rate_per_minute: float,
    sequence_start: int,
) -> list[dict]:
    interval = 60.0 / rate_per_minute
    window_started = time.perf_counter()
    futures = []
    for index in range(requests):
        scheduled_at = window_started + index * interval
        delay = scheduled_at - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        sequence = sequence_start + index
        client_id = (index % clients) + 1
        futures.append(
            pool.submit(
                call_server,
                client_id,
                sequence,
                window,
                rate_per_minute,
                server,
                model,
                signal,
                signal_file,
            )
        )
    return [future.result() for future in as_completed(futures)]


def run_fixed(args: argparse.Namespace, targets: list[str], signal: np.ndarray, signal_file: str) -> tuple[list[dict], list[dict]]:
    rate = args.rate_per_minute or float(LOAD_CONFIG["initial_rate_per_minute"])
    requests = args.requests or math.ceil(rate * float(args.window_seconds) / 60.0)
    rows: list[dict] = []
    windows: list[dict] = []
    sequence = 1
    with ThreadPoolExecutor(max_workers=args.clients) as pool:
        for target in targets:
            batch = run_window(pool, target, args.model, signal, signal_file, args.clients, 1, requests, rate, sequence)
            sequence += requests
            rows.extend(batch)
            summary = window_summary(batch)
            healthy, reason = is_healthy(summary, args)
            print(
                "[load] "
                f"server={target} mode=fixed rate={rate:.1f}/min "
                f"ok={summary['ok']}/{summary['total']} p95={summary['p95_roundtrip_ms']} "
                f"queue={summary['avg_queue_ms']} healthy={healthy}"
            )
            windows.append(
                {
                    "server": target,
                    "window": 1,
                    "planned_rate_per_minute": rate,
                    "requests": requests,
                    "healthy": healthy,
                    "reason": reason,
                    **summary,
                }
            )
    return rows, windows


def run_adaptive(args: argparse.Namespace, targets: list[str], signal: np.ndarray, signal_file: str) -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    windows: list[dict] = []
    sequence = 1
    initial_rate = args.initial_rate_per_minute
    max_rate = args.max_rate_per_minute
    target_rate = args.target_rate_per_minute
    with ThreadPoolExecutor(max_workers=args.clients) as pool:
        for target in targets:
            rate = initial_rate
            best_stable_rate = 0.0
            overload_windows = 0
            for window in range(1, args.max_windows + 1):
                requests = max(args.clients, math.ceil(rate * float(args.window_seconds) / 60.0))
                batch = run_window(
                    pool,
                    target,
                    args.model,
                    signal,
                    signal_file,
                    args.clients,
                    window,
                    requests,
                    rate,
                    sequence,
                )
                sequence += requests
                rows.extend(batch)
                summary = window_summary(batch)
                healthy, reason = is_healthy(summary, args)
                if healthy:
                    best_stable_rate = max(best_stable_rate, rate)
                    overload_windows = 0
                    next_rate = min(max_rate, rate * args.step_up_factor)
                    decision = "aumentar" if next_rate > rate else "manter"
                else:
                    overload_windows += 1
                    next_rate = max(initial_rate, rate * args.step_down_factor)
                    decision = "reduzir"

                windows.append(
                    {
                        "server": target,
                        "window": window,
                        "planned_rate_per_minute": rate,
                        "next_rate_per_minute": next_rate,
                        "target_rate_per_minute": target_rate,
                        "best_stable_rate_per_minute": best_stable_rate or None,
                        "requests": requests,
                        "healthy": healthy,
                        "reason": reason,
                        "decision": decision,
                        **summary,
                    }
                )
                print(
                    "[load] "
                    f"server={target} window={window} rate={rate:.1f}/min "
                    f"ok={summary['ok']}/{summary['total']} p95={summary['p95_roundtrip_ms']} "
                    f"queue={summary['avg_queue_ms']} decision={decision}"
                )

                if not healthy and overload_windows >= 2 and best_stable_rate:
                    break
                if healthy and target_rate and rate >= target_rate and window >= 2:
                    next_rate = min(max_rate, rate * args.step_up_factor)
                if healthy and rate >= max_rate:
                    break
                rate = next_rate
    return rows, windows


def write_reports(
    rows: list[dict],
    windows: list[dict],
    summary: dict[str, object],
    args: argparse.Namespace,
    gain_label: str,
    resource_samples: list[dict],
    resource_summary: dict[str, object],
    machine_summary: dict[str, object] | None = None,
) -> tuple[Path, Path, Path]:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    csv_path = OUTPUT_DIR / f"load-{timestamp}.csv"
    windows_path = OUTPUT_DIR / f"load-windows-{timestamp}.csv"
    resources_path = OUTPUT_DIR / f"load-resources-{timestamp}.csv"
    md_path = OUTPUT_DIR / f"load-{timestamp}.md"
    headers = list(rows[0].keys()) if rows else []
    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

    resource_headers = [
        "timestamp", "server", "queue_size", "active_jobs", "completed_jobs",
        "rejected_jobs", "rss_bytes", "rss_mb", "cpu_process_ms", "cpu_percent",
        "machine_cpu_percent", "machine_mem_used_mb", "machine_mem_total_mb", "machine_mem_load_percent",
    ]
    with resources_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=resource_headers)
        writer.writeheader()
        writer.writerows(resource_samples)

    window_headers = list(windows[0].keys()) if windows else []
    with windows_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=window_headers)
        writer.writeheader()
        writer.writerows(windows)

    stable_by_server: dict[str, float] = {}
    overload_by_server: dict[str, float] = {}
    for window in windows:
        server = str(window["server"])
        rate = float(window["planned_rate_per_minute"])
        if window["healthy"]:
            stable_by_server[server] = max(stable_by_server.get(server, 0.0), rate)
        else:
            overload_by_server[server] = min(overload_by_server.get(server, rate), rate)

    with md_path.open("w", encoding="utf-8") as file:
        title = "Teste de saturacao adaptativo" if args.mode == "adaptive" else "Teste de saturacao fixo"
        file.write(f"# {title}\n\n")
        file.write(f"Gerado em: {datetime.now().isoformat(timespec='seconds')}\n\n")
        file.write(f"- Modo: `{args.mode}`\n")
        file.write(f"- Servidor alvo: `{args.server}`\n")
        file.write(f"- Modelo: `{args.model}`\n")
        file.write(f"- Clientes simultaneos: `{args.clients}`\n")
        file.write(f"- Janela de avaliacao: `{args.window_seconds}` s\n")
        file.write(f"- Ganho aplicado: `{gain_label}`\n")
        if args.mode == "fixed":
            file.write(f"- Taxa fixa planejada: `{args.rate_per_minute:.2f}` req/min\n")
            if args.requests:
                file.write(f"- Requisicoes planejadas por servidor: `{args.requests}`\n")
        else:
            file.write(f"- Taxa inicial: `{args.initial_rate_per_minute:.2f}` req/min\n")
            file.write(f"- Taxa maxima permitida no teste: `{args.max_rate_per_minute:.2f}` req/min\n")
        if args.target_rate_per_minute:
            file.write(f"- Taxa alvo demonstrativa: `{args.target_rate_per_minute:.2f}` req/min\n")
        file.write(
            f"- Criterio saudavel: erro <= `{args.max_error_rate:.2%}`, "
            f"p95 <= `{args.max_p95_roundtrip_ms:.0f}` ms, "
            f"fila media <= `{args.max_avg_queue_ms:.0f}` ms\n\n"
        )

        file.write("## Resumo geral\n\n")
        for key, value in summary.items():
            if isinstance(value, float):
                file.write(f"- {key}: {value:.3f}\n")
            else:
                file.write(f"- {key}: {value}\n")

        file.write("\n## Limite observado\n\n")
        for server in sorted(set(stable_by_server) | set(overload_by_server)):
            stable = stable_by_server.get(server)
            overload = overload_by_server.get(server)
            file.write(
                f"- `{server}`: melhor taxa saudavel "
                f"`{stable:.2f} req/min`" if stable else f"- `{server}`: sem janela saudavel"
            )
            if overload:
                file.write(f"; primeira degradacao em `{overload:.2f} req/min`")
            file.write(".\n")

        file.write("\n## Janelas\n\n")
        file.write("| Servidor | Janela | Taxa | Requisicoes | OK | Erro | P95 ms | Fila ms | Decisao |\n")
        file.write("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |\n")
        for window in windows:
            file.write(
                "| {server} | {window} | {rate:.2f} | {requests} | {ok}/{total} | {error:.2%} | {p95} | {queue} | {decision} |\n".format(
                    server=window["server"],
                    window=window["window"],
                    rate=float(window["planned_rate_per_minute"]),
                    requests=window["requests"],
                    ok=window["ok"],
                    total=window["total"],
                    error=float(window["error_rate"]),
                    p95="-" if window["p95_roundtrip_ms"] is None else f"{float(window['p95_roundtrip_ms']):.1f}",
                    queue="-" if window["avg_queue_ms"] is None else f"{float(window['avg_queue_ms']):.1f}",
                    decision=window.get("decision", "fixo"),
                )
            )

        file.write("\n## Recursos (CPU e memoria)\n\n")
        file.write(
            "Medidos durante o teste por amostragem do endpoint `/status` de cada servidor "
            "(serie temporal em `" + resources_path.name + "`).\n\n"
        )
        if resource_summary:
            file.write("| Servidor | Amostras | RSS pico MB | RSS medio MB | CPU pico % | CPU medio % | Fila pico |\n")
            file.write("| --- | ---: | ---: | ---: | ---: | ---: | ---: |\n")
            for server, stats in resource_summary.items():
                file.write(
                    "| {server} | {samples} | {rss_peak} | {rss_avg} | {cpu_peak} | {cpu_avg} | {queue} |\n".format(
                        server=server,
                        samples=stats.get("samples"),
                        rss_peak="-" if stats.get("rss_peak_mb") is None else stats["rss_peak_mb"],
                        rss_avg="-" if stats.get("rss_avg_mb") is None else stats["rss_avg_mb"],
                        cpu_peak="-" if stats.get("cpu_peak_percent") is None else stats["cpu_peak_percent"],
                        cpu_avg="-" if stats.get("cpu_avg_percent") is None else stats["cpu_avg_percent"],
                        queue="-" if stats.get("queue_peak") is None else stats["queue_peak"],
                    )
                )
            file.write(
                "\nObservacao: no servidor C++ (gateway), a CPU do processo Python nao reflete o calculo, "
                "que roda no binario C++ via WSL; a CPU real do C++ aparece por requisicao na coluna `cpu_ms` do CSV de carga. "
                "Por isso a linha de recursos do C++ pode parecer baixa: o peso esta na maquina inteira (abaixo).\n"
            )
        else:
            file.write("Nenhuma amostra de recurso coletada.\n")

        if machine_summary:
            file.write("\n## Recursos da maquina inteira (CPU e memoria totais)\n\n")
            file.write(
                "Medidos com a API do Windows (CPU% de toda a maquina e RAM fisica usada), "
                "incluindo o binario C++ que roda no WSL e nao aparece no RSS do gateway.\n\n"
            )
            file.write("| Metrica | Valor |\n| --- | ---: |\n")
            rows_machine = [
                ("Amostras", machine_summary.get("samples")),
                ("CPU maquina pico %", machine_summary.get("cpu_peak_percent")),
                ("CPU maquina media %", machine_summary.get("cpu_avg_percent")),
                ("RAM usada pico MB", machine_summary.get("mem_peak_used_mb")),
                ("RAM usada media MB", machine_summary.get("mem_avg_used_mb")),
                ("RAM total da maquina MB", machine_summary.get("mem_total_mb")),
                ("Carga de RAM pico %", machine_summary.get("mem_load_peak_percent")),
            ]
            for label, value in rows_machine:
                file.write(f"| {label} | {'-' if value is None else value} |\n")

        file.write("\n## Controle de saturacao\n\n")
        file.write(
            "A carga nao e fixa: o cliente aumenta ou reduz a taxa de envio conforme as metricas de cada janela. "
            "O servidor, por sua vez, protege o processamento com fila limitada, pool de workers "
            "e limite de memoria estimado antes de aceitar novos trabalhos (rejeitando com HTTP 503 quando excede).\n"
        )
        file.write(f"\nArquivos: `{csv_path.name}`, `{windows_path.name}` e `{resources_path.name}`.\n")
    return csv_path, md_path, resources_path


def start_server_process(target: str) -> tuple[subprocess.Popen, object]:
    """Sobe um servidor (Python ou gateway C++) como processo proprio."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{target}-server.log"
    log_file = log_path.open("w", encoding="utf-8")
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", SERVER_MODULES[target]],
        cwd=ROOT,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    return proc, log_file


def kill_port(port: int) -> None:
    """Derruba qualquer processo escutando na porta (Windows), garantindo isolamento."""
    if os.name != "nt":
        return
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout
    except Exception:
        return
    for line in out.splitlines():
        if f":{port} " in line and "LISTENING" in line:
            parts = line.split()
            if parts:
                subprocess.run(["taskkill", "/F", "/T", "/PID", parts[-1]], capture_output=True)


def stop_all_servers() -> None:
    """Garante que NENHUM servidor (Python/C++) esteja no ar antes de isolar um."""
    for cfg in (CONFIG["server"]["python"], CONFIG["server"]["cpp"]):
        kill_port(int(cfg["port"]))


def wait_online(target: str, timeout: float = 40.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if fetch_status(target, timeout=2.0):
            return True
        time.sleep(1.0)
    return False


def stop_server_process(proc: subprocess.Popen, log_file: object) -> None:
    """Encerra o servidor e TODA a arvore (inclui o wsl.exe/cgnr_cpp do C++)."""
    if proc.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
    try:
        log_file.close()  # type: ignore[attr-defined]
    except OSError:
        pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", choices=["python", "cpp", "both"], default="python")
    parser.add_argument("--model", choices=CONFIG["models"].keys(), default="30x30")
    parser.add_argument("--mode", choices=["adaptive", "fixed"], default="adaptive")
    parser.add_argument("--clients", default="auto")
    parser.add_argument("--requests", type=int)
    parser.add_argument("--rate-per-minute", type=float)
    parser.add_argument("--target-rate-per-minute", type=float)
    parser.add_argument("--initial-rate-per-minute", type=float, default=float(LOAD_CONFIG["initial_rate_per_minute"]))
    parser.add_argument("--max-rate-per-minute", type=float, default=float(LOAD_CONFIG["max_rate_per_minute"]))
    parser.add_argument("--window-seconds", type=int, default=int(LOAD_CONFIG["window_seconds"]))
    parser.add_argument("--max-windows", type=int, default=int(LOAD_CONFIG["max_windows"]))
    parser.add_argument("--step-up-factor", type=float, default=float(LOAD_CONFIG["step_up_factor"]))
    parser.add_argument("--step-down-factor", type=float, default=float(LOAD_CONFIG["step_down_factor"]))
    parser.add_argument("--max-error-rate", type=float, default=float(LOAD_CONFIG["max_error_rate"]))
    parser.add_argument("--max-p95-roundtrip-ms", type=float, default=float(LOAD_CONFIG["max_p95_roundtrip_ms"]))
    parser.add_argument("--max-avg-queue-ms", type=float, default=float(LOAD_CONFIG["max_avg_queue_ms"]))
    parser.add_argument("--gain", choices=["none", "scalar", "formula"], default="scalar")
    # Um servidor por vez: liga o alvo, roda, desliga antes do proximo (isolamento
    # justo e libera a RAM). Use --no-manage-servers para testar servidores ja no ar.
    parser.add_argument("--manage-servers", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    args.clients = resolve_clients(str(args.clients))
    if args.mode == "fixed" and not args.rate_per_minute:
        args.rate_per_minute = args.target_rate_per_minute or args.initial_rate_per_minute
    if args.target_rate_per_minute and args.max_rate_per_minute < args.target_rate_per_minute:
        args.max_rate_per_minute = args.target_rate_per_minute
    if args.initial_rate_per_minute <= 0 or args.max_rate_per_minute <= 0:
        raise SystemExit("taxas precisam ser maiores que zero")
    if args.window_seconds < 1 or args.max_windows < 1:
        raise SystemExit("janela e quantidade de janelas precisam ser maiores que zero")
    if args.step_up_factor <= 1.0:
        raise SystemExit("--step-up-factor precisa ser maior que 1")
    if not 0 < args.step_down_factor < 1:
        raise SystemExit("--step-down-factor precisa ficar entre 0 e 1")
    return args


def main() -> None:
    args = parse_args()
    model = CONFIG["models"][args.model]
    signal_path = resolve_project_path(random.choice(model["signals"][:2]))
    original_signal = read_vector(signal_path)
    signal, gain_label = apply_gain(original_signal, args.gain)
    targets = ["python", "cpp"] if args.server == "both" else [args.server]

    print(
        "[load] "
        f"mode={args.mode} targets={','.join(targets)} model={args.model} clients={args.clients} "
        f"initial={args.initial_rate_per_minute}/min max={args.max_rate_per_minute}/min "
        f"signal={signal_path.name} gain={gain_label}"
    )

    if args.manage_servers:
        print("[load] modo um-servidor-por-vez: ligo, testo e desligo cada servidor em sequencia.")

    sampler = ResourceSampler(targets, interval=0.5)
    sampler.start()
    started = time.perf_counter()
    rows: list[dict] = []
    windows: list[dict] = []
    try:
        for target in targets:
            proc = None
            log_file = None
            if args.manage_servers:
                # Isolamento garantido: derruba TUDO e sobe so o alvo desta fase.
                print(f"[load] garantindo isolamento: so o {target} vai ficar no ar...")
                stop_all_servers()
                time.sleep(1.0)
                proc, log_file = start_server_process(target)
                if not wait_online(target, timeout=40.0):
                    print(f"[load] {target} nao respondeu a tempo; pulando este servidor.")
                    stop_server_process(proc, log_file)
                    continue
                print(f"[load] {target} online (sozinho).")

            if args.mode == "adaptive":
                target_rows, target_windows = run_adaptive(args, [target], signal, signal_path.name)
            else:
                target_rows, target_windows = run_fixed(args, [target], signal, signal_path.name)
            rows.extend(target_rows)
            windows.extend(target_windows)

            if args.manage_servers and proc is not None:
                print(f"[load] desligando {target} (liberando memoria antes do proximo)...")
                stop_server_process(proc, log_file)
                time.sleep(1.5)
    finally:
        sampler.stop()
        sampler.join(timeout=2.0)

    elapsed = time.perf_counter() - started
    rows.sort(key=lambda row: (row["server"], int(row["window"]), int(row["sequence"]), int(row["client_id"])))
    summary = summarize(rows, elapsed)
    resource_summary = sampler.summary()
    machine_summary = sampler.machine_summary()
    csv_path, md_path, resources_path = write_reports(
        rows, windows, summary, args, gain_label, sampler.samples, resource_summary, machine_summary
    )
    print(f"CSV: {csv_path}")
    print(f"Markdown: {md_path}")
    print(f"Recursos: {resources_path}")


if __name__ == "__main__":
    main()
