from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
import uuid
from datetime import datetime
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .io_data import save_png_from_pgm
from .metrics import current_rss_bytes, resolve_max_workers


ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
OUTPUT_DIR = ROOT / "outputs" / "cpp_gateway"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR = ROOT / "data"


@dataclass
class GatewayJob:
    job_id: str
    model_id: str
    signal_bytes: bytes
    arrived_at: float
    event: threading.Event
    result: dict[str, Any] | None = None
    error: str | None = None


class GatewayState:
    def __init__(self) -> None:
        self.max_workers = resolve_max_workers(CONFIG["server"]["cpp"]["max_workers"])
        self.queue_limit = int(CONFIG["server"]["queue_limit"])
        self.jobs: queue.Queue[GatewayJob] = queue.Queue(maxsize=self.queue_limit)
        self.slots = threading.BoundedSemaphore(self.max_workers)
        self.active_jobs = 0
        self.completed_jobs = 0
        self.rejected_jobs = 0
        self.lock = threading.Lock()
        self.workers: list[threading.Thread] = []
        self.shutdown_event = threading.Event()

    def start_workers(self) -> None:
        if self.workers:
            return
        for index in range(self.max_workers):
            worker = threading.Thread(target=self.worker_loop, name=f"cpp-gateway-worker-{index}", daemon=True)
            worker.start()
            self.workers.append(worker)

    def estimate_memory_bytes(self, model_id: str) -> int:
        model = CONFIG["models"][model_id]
        rows = int(model["rows"])
        cols = int(model["cols"])
        matrix = rows * cols * 8
        vectors = (rows * 2 + cols * 4) * 8
        return matrix + vectors

    def can_accept(self, model_id: str) -> tuple[bool, str]:
        if self.jobs.qsize() >= self.queue_limit:
            return False, "queue limit reached"
        estimated = self.estimate_memory_bytes(model_id)
        memory_limit = int(CONFIG["server"]["memory_soft_limit_bytes"])
        rss = current_rss_bytes()
        with self.lock:
            projected = rss + (self.active_jobs + 1) * estimated
        if rss and projected > memory_limit:
            return False, "memory soft limit reached"
        return True, "accepted"

    def enqueue_and_wait(self, model_id: str, signal_bytes: bytes, job_id: str) -> dict[str, Any]:
        ok, reason = self.can_accept(model_id)
        if not ok:
            with self.lock:
                self.rejected_jobs += 1
            raise RuntimeError(reason)

        job = GatewayJob(
            job_id=job_id,
            model_id=model_id,
            signal_bytes=signal_bytes,
            arrived_at=time.perf_counter(),
            event=threading.Event(),
        )
        self.jobs.put(job)
        timeout = float(CONFIG["server"]["request_timeout_seconds"])
        if not job.event.wait(timeout):
            raise TimeoutError("request timed out while waiting in gateway queue")
        if job.error:
            raise RuntimeError(job.error)
        assert job.result is not None
        return job.result

    def worker_loop(self) -> None:
        while not self.shutdown_event.is_set():
            try:
                job = self.jobs.get(timeout=0.2)
            except queue.Empty:
                continue

            self.slots.acquire()
            with self.lock:
                self.active_jobs += 1
            queue_ms = (time.perf_counter() - job.arrived_at) * 1000.0
            try:
                job.result = run_cpp_reconstruction(job.model_id, job.signal_bytes, job.job_id)
                job.result["queue_ms"] = queue_ms
            except Exception as exc:
                job.error = str(exc)
            finally:
                with self.lock:
                    self.active_jobs -= 1
                    self.completed_jobs += 1
                self.slots.release()
                job.event.set()
                self.jobs.task_done()

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "server": "cpp-gateway",
                "queue_size": self.jobs.qsize(),
                "active_jobs": self.active_jobs,
                "completed_jobs": self.completed_jobs,
                "rejected_jobs": self.rejected_jobs,
                "max_workers": self.max_workers,
                "rss_bytes": current_rss_bytes(),
                "cpu_process_ms": time.process_time() * 1000.0,
                "time": datetime.now().isoformat(timespec="seconds"),
            }


STATE = GatewayState()


def windows_to_wsl(path: Path) -> str:
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    rest = str(resolved)[3:].replace("\\", "/")
    return f"/mnt/{drive}/{rest}"


def run_cpp_reconstruction(model_id: str, signal_bytes: bytes, job_id: str) -> dict[str, Any]:
    if model_id not in CONFIG["models"]:
        raise ValueError(f"unknown model_id {model_id}")

    model = CONFIG["models"][model_id]
    expected_bytes = int(model["rows"]) * 8
    if len(signal_bytes) != expected_bytes:
        raise ValueError(f"signal body has {len(signal_bytes)} bytes; expected {expected_bytes}")

    signal_path = DATA_DIR / f"{job_id}.signal.f64"
    signal_path.write_bytes(signal_bytes)

    image_path = OUTPUT_DIR / f"{job_id}-{model_id}.pgm"
    json_path = OUTPUT_DIR / f"{job_id}-{model_id}.json"
    h_path = ROOT / model["h_bin"]
    if not h_path.exists():
        raise FileNotFoundError(f"H binary not found: {h_path}. Run scripts/prepare_binary_data.py first.")

    bash_cmd = (
        f"cd {windows_to_wsl(ROOT)} && "
        f"./build/cgnr_cpp "
        f"--h {windows_to_wsl(h_path)} "
        f"--g {windows_to_wsl(signal_path)} "
        f"--rows {model['rows']} "
        f"--cols {model['cols']} "
        f"--width {model['width']} "
        f"--height {model['height']} "
        f"--max-iterations {CONFIG['max_iterations']} "
        f"--tolerance {CONFIG['tolerance']} "
        f"--image-out {windows_to_wsl(image_path)} "
        f"--json-out {windows_to_wsl(json_path)}"
    )
    completed = subprocess.run(
        ["wsl", "-d", "Codex-Debian", "--exec", "bash", "-lc", bash_cmd],
        capture_output=True,
        text=True,
        timeout=float(CONFIG["server"]["request_timeout_seconds"]),
    )
    if completed.returncode != 0:
        raise RuntimeError(f"C++ reconstruction failed: {completed.stderr or completed.stdout}")

    result = json.loads(json_path.read_text(encoding="utf-8"))
    result.update(
        {
            "job_id": job_id,
            "server": "cpp",
            "model_id": model_id,
            "resolution": [model["width"], model["height"]],
            "queue_ms": 0.0,
            "reconstruction_ms": result.get("metrics", {}).get("wall_ms"),
            "cpu_ms": result.get("metrics", {}).get("cpu_ms"),
            "rss_end_kb": result.get("metrics", {}).get("max_rss_kb"),
            "image_path": str(image_path),
            "status": "ok",
        }
    )
    # PNG com os metadados desenhados de forma visivel, a partir do PGM gerado pelo C++.
    try:
        png_path = image_path.with_suffix(".png")
        save_png_from_pgm(
            png_path,
            image_path,
            [
                "Algoritmo: CGNR (C++/OpenBLAS)",
                f"Inicio:  {result.get('started_at', '')}",
                f"Termino: {result.get('ended_at', '')}",
                f"Tamanho: {model['width']}x{model['height']} pixels",
                f"Iteracoes: {result.get('iterations', '')}",
            ],
        )
    except Exception:
        pass
    return result


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/status":
            self.send_json(200, STATE.status())
            return
        if self.path == "/health":
            self.send_json(200, {"ok": True, "server": "cpp-gateway"})
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/reconstruct":
            self.send_json(404, {"error": "not found"})
            return
        job_id = self.headers.get("X-Job-Id") or str(uuid.uuid4())
        model_id = self.headers.get("X-Model-Id") or ""
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)

        started = time.perf_counter()
        try:
            result = STATE.enqueue_and_wait(model_id, body, job_id)
            result["roundtrip_gateway_ms"] = (time.perf_counter() - started) * 1000.0
            self.send_json(200, result)
        except TimeoutError as exc:
            self.send_json(504, {"server": "cpp", "status": "timeout", "error": str(exc)})
        except Exception as exc:
            self.send_json(503, {"server": "cpp", "status": "error", "error": str(exc)})

    def send_json(self, status: int, data: dict[str, Any]) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def main() -> None:
    host = CONFIG["server"]["cpp"]["host"]
    port = int(CONFIG["server"]["cpp"]["port"])
    STATE.start_workers()
    print(f"C++ gateway listening on http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
