from __future__ import annotations

import argparse
import csv
import json
import random
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
OUTPUT_DIR = ROOT / "outputs" / "client"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


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


def post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=300) as response:
        return json.loads(response.read().decode("utf-8"))


def post_binary(url: str, signal: np.ndarray, model_id: str, job_id: str) -> dict:
    body = np.asarray(signal, dtype=np.float64).tobytes()
    req = Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/octet-stream",
            "X-Model-Id": model_id,
            "X-Job-Id": job_id,
        },
        method="POST",
    )
    with urlopen(req, timeout=300) as response:
        return json.loads(response.read().decode("utf-8"))


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


def call_server(name: str, model_id: str, signal: np.ndarray, job_id: str) -> dict:
    started = time.perf_counter()
    try:
        if name == "python":
            response = post_json(
                f"http://{CONFIG['server']['python']['host']}:{CONFIG['server']['python']['port']}/reconstruct",
                {"model_id": model_id, "g": signal.tolist(), "job_id": job_id},
            )
        elif name == "cpp":
            response = post_binary(
                f"{cpp_base_url()}/reconstruct",
                signal,
                model_id,
                job_id,
            )
        else:
            raise ValueError(name)
        response["roundtrip_ms"] = (time.perf_counter() - started) * 1000.0
        response["status"] = "ok"
        return response
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {
            "server": name,
            "status": "http_error",
            "http_status": exc.code,
            "error": body,
            "roundtrip_ms": (time.perf_counter() - started) * 1000.0,
        }
    except URLError as exc:
        return {
            "server": name,
            "status": "connection_error",
            "error": str(exc),
            "roundtrip_ms": (time.perf_counter() - started) * 1000.0,
        }


def flatten_result(run_id: str, signal_file: str, gain_label: str, result: dict) -> dict:
    return {
        "run_id": run_id,
        "server": result.get("server"),
        "status": result.get("status"),
        "model_id": result.get("model_id"),
        "signal_file": signal_file,
        "gain": gain_label,
        "iterations": result.get("iterations"),
        "error_abs": result.get("error_abs"),
        "residual_norm": result.get("residual_norm"),
        "lambda": result.get("lambda"),
        "reduction_factor_estimate": result.get("reduction_factor_estimate"),
        "queue_ms": result.get("queue_ms"),
        "reconstruction_ms": result.get("reconstruction_ms"),
        "roundtrip_ms": result.get("roundtrip_ms"),
        "cpu_ms": result.get("cpu_ms"),
        "rss_end_bytes": result.get("rss_end_bytes"),
        "rss_end_kb": result.get("rss_end_kb"),
        "image_path": result.get("image_path"),
        "error": result.get("error"),
    }


def write_reports(rows: list[dict]) -> tuple[Path, Path]:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    csv_path = OUTPUT_DIR / f"comparison-{timestamp}.csv"
    md_path = OUTPUT_DIR / f"comparison-{timestamp}.md"
    headers = list(rows[0].keys()) if rows else []
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

    with md_path.open("w", encoding="utf-8") as f:
        f.write("# Relatorio comparativo CGNR\n\n")
        f.write(f"Gerado em: {datetime.now().isoformat(timespec='seconds')}\n\n")
        f.write("| Run | Servidor | Modelo | Status | Iteracoes | Reconstrucao ms | Roundtrip ms | Erro | Imagem |\n")
        f.write("| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |\n")
        for row in rows:
            f.write(
                "| {run_id} | {server} | {model_id} | {status} | {iterations} | {reconstruction_ms} | {roundtrip_ms} | {error_abs} | `{image_path}` |\n".format(
                    **{key: "" if value is None else value for key, value in row.items()}
                )
            )
    return csv_path, md_path


def main() -> None:
    model_keys = list(CONFIG["models"].keys())
    gain_modes = ["none", "scalar", "formula"]

    parser = argparse.ArgumentParser()
    # "random" e o padrao: o enunciado exige que o ganho E o modelo sejam
    # definidos aleatoriamente. Passar um modelo/ganho fixo e apenas um override.
    parser.add_argument("--model", default="random", choices=model_keys + ["random"])
    parser.add_argument("--count", type=int, default=2)
    parser.add_argument("--gain", choices=gain_modes + ["random"], default="random")
    parser.add_argument("--servers", default="python,cpp", help="lista separada por virgula: python,cpp")
    args = parser.parse_args()

    servers = [name.strip() for name in args.servers.split(",") if name.strip()]
    rows: list[dict] = []

    for index in range(args.count):
        # Sorteio por envio: modelo da imagem e modo de ganho.
        model_key = random.choice(model_keys) if args.model == "random" else args.model
        gain_mode = random.choice(gain_modes) if args.gain == "random" else args.gain

        model = CONFIG["models"][model_key]
        signals = [resolve_project_path(path) for path in model["signals"]]
        signal_path = random.choice(signals[:2])
        original_signal = read_vector(signal_path)
        signal, gain_label = apply_gain(original_signal, gain_mode)
        run_id = str(uuid.uuid4())

        print(f"[cliente] run={index + 1}/{args.count} model={model_key} signal={signal_path.name} gain={gain_label}")
        # A MESMA sequencia de sinal (mesmo run_id, mesmo g) vai para os dois servidores.
        for server_name in servers:
            result = call_server(server_name, model_key, signal, run_id)
            rows.append(flatten_result(run_id, signal_path.name, gain_label, result))
            print(f"  {server_name}: {result.get('status')} {result.get('reconstruction_ms')} ms")

        if index < args.count - 1:
            # Intervalo de tempo aleatorio entre envios.
            time.sleep(random.uniform(1.0, 4.0))

    csv_path, md_path = write_reports(rows)
    print(f"CSV: {csv_path}")
    print(f"Markdown: {md_path}")


if __name__ == "__main__":
    main()
