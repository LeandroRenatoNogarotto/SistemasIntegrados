from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np

from .cgnr import cgnr
from .io_data import load_h_from_zip, save_pgm, save_png_annotated
from .metrics import current_rss_bytes, resolve_max_workers


ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
OUTPUT_DIR = ROOT / "outputs" / "python_server"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Job:
    job_id: str
    model_id: str
    signal: np.ndarray
    arrived_at: float
    event: threading.Event
    result: dict[str, Any] | None = None
    error: str | None = None


class ModelCache:
    def __init__(self) -> None:
        self._models: dict[str, np.ndarray] = {}
        self._locks: dict[str, threading.Lock] = {}

    def get(self, model_id: str) -> np.ndarray:
        if model_id in self._models:
            return self._models[model_id]
        self._locks.setdefault(model_id, threading.Lock())
        with self._locks[model_id]:
            if model_id in self._models:
                return self._models[model_id]
            model = CONFIG["models"][model_id]
            h_zip = (ROOT / model["h_zip"]).resolve()
            h_csv = model["h_csv"]
            cache_path = ROOT / "data" / f"{Path(h_csv).stem}.npy"
            matrix = load_h_from_zip(h_zip, h_csv, cache_path)
            self._models[model_id] = matrix
            return matrix


class ResourceController:
    def __init__(self) -> None:
        self.max_workers = resolve_max_workers(CONFIG["server"]["python"]["max_workers"])
        self.queue_limit = int(CONFIG["server"]["queue_limit"])
        self.active_jobs = 0
        self.completed_jobs = 0
        self.rejected_jobs = 0
        self.slots = threading.BoundedSemaphore(self.max_workers)
        self.lock = threading.Lock()

    def estimate_memory_bytes(self, model_id: str) -> int:
        model = CONFIG["models"][model_id]
        rows = int(model["rows"])
        cols = int(model["cols"])
        matrix = rows * cols * 8
        vectors = (rows * 2 + cols * 4) * 8
        return matrix + vectors

    def can_accept(self, job_queue: queue.Queue[Job], model_id: str) -> tuple[bool, str]:
        if job_queue.qsize() >= self.queue_limit:
            return False, "queue limit reached"
        estimated = self.estimate_memory_bytes(model_id)
        memory_limit = int(CONFIG["server"]["memory_soft_limit_bytes"])
        rss = current_rss_bytes()
        if rss and rss + estimated > memory_limit:
            return False, "memory soft limit reached"
        return True, "accepted"


class ReconstructionServer:
    def __init__(self) -> None:
        self.models = ModelCache()
        self.controller = ResourceController()
        self.jobs: queue.Queue[Job] = queue.Queue(maxsize=self.controller.queue_limit)
        self.shutdown_event = threading.Event()
        self.workers: list[threading.Thread] = []

    def start_workers(self) -> None:
        for index in range(self.controller.max_workers):
            worker = threading.Thread(target=self.worker_loop, name=f"python-cgnr-worker-{index}", daemon=True)
            worker.start()
            self.workers.append(worker)

    def worker_loop(self) -> None:
        while not self.shutdown_event.is_set():
            try:
                job = self.jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            self.controller.slots.acquire()
            with self.controller.lock:
                self.controller.active_jobs += 1
            try:
                job.result = self.process(job)
            except Exception as exc:
                job.error = str(exc)
            finally:
                with self.controller.lock:
                    self.controller.active_jobs -= 1
                    self.controller.completed_jobs += 1
                self.controller.slots.release()
                job.event.set()
                self.jobs.task_done()

    def enqueue_and_wait(self, model_id: str, signal: np.ndarray) -> dict[str, Any]:
        ok, reason = self.controller.can_accept(self.jobs, model_id)
        if not ok:
            with self.controller.lock:
                self.controller.rejected_jobs += 1
            raise RuntimeError(reason)

        job = Job(
            job_id=str(uuid.uuid4()),
            model_id=model_id,
            signal=signal,
            arrived_at=time.perf_counter(),
            event=threading.Event(),
        )
        self.jobs.put(job)
        timeout = float(CONFIG["server"]["request_timeout_seconds"])
        if not job.event.wait(timeout):
            raise TimeoutError("request timed out while waiting in server queue")
        if job.error:
            raise RuntimeError(job.error)
        assert job.result is not None
        return job.result

    def process(self, job: Job) -> dict[str, Any]:
        model = CONFIG["models"][job.model_id]
        width = int(model["width"])
        height = int(model["height"])
        h = self.models.get(job.model_id)
        expected_rows = h.shape[0]
        if job.signal.shape[0] != expected_rows:
            raise ValueError(f"signal length {job.signal.shape[0]} != expected {expected_rows}")

        queue_ms = (time.perf_counter() - job.arrived_at) * 1000.0
        result = cgnr(
            h,
            job.signal,
            max_iterations=int(CONFIG["max_iterations"]),
            tolerance=float(CONFIG["tolerance"]),
        )

        image_path = OUTPUT_DIR / f"{job.job_id}-{job.model_id}.pgm"
        metadata_lines = [
            "algorithm=CGNR",
            "language=Python",
            f"job_id={job.job_id}",
            f"started_at={result.started_at.isoformat(timespec='seconds')}",
            f"ended_at={result.ended_at.isoformat(timespec='seconds')}",
            f"resolution={width}x{height}",
            f"iterations={result.iterations}",
        ]
        save_pgm(image_path, result.image, width, height, metadata_lines)
        # PNG com os metadados desenhados de forma visivel (alem do cabecalho PGM).
        png_path = image_path.with_suffix(".png")
        save_png_annotated(
            png_path,
            result.image,
            width,
            height,
            [
                "Algoritmo: CGNR (Python/NumPy)",
                f"Inicio:  {result.started_at.isoformat(timespec='seconds')}",
                f"Termino: {result.ended_at.isoformat(timespec='seconds')}",
                f"Tamanho: {width}x{height} pixels",
                f"Iteracoes: {result.iterations}",
            ],
        )

        return {
            "job_id": job.job_id,
            "server": "python",
            "algorithm": "CGNR",
            "model_id": job.model_id,
            "resolution": [width, height],
            "iterations": result.iterations,
            "error_abs": result.error_abs,
            "error_signed": result.error_signed,
            "residual_norm": result.residual_norm,
            "lambda": result.lambda_value,
            "reduction_factor_estimate": result.reduction_factor_estimate,
            "queue_ms": queue_ms,
            "reconstruction_ms": result.metrics["wall_ms"],
            "cpu_ms": result.metrics["cpu_ms"],
            "rss_start_bytes": result.metrics["rss_start_bytes"],
            "rss_end_bytes": result.metrics["rss_end_bytes"],
            "started_at": result.started_at.isoformat(timespec="seconds"),
            "ended_at": result.ended_at.isoformat(timespec="seconds"),
            "image_path": str(image_path),
        }

    def status(self) -> dict[str, Any]:
        with self.controller.lock:
            active = self.controller.active_jobs
            completed = self.controller.completed_jobs
            rejected = self.controller.rejected_jobs
        return {
            "server": "python",
            "time": datetime.now().isoformat(timespec="seconds"),
            "queue_size": self.jobs.qsize(),
            "active_jobs": active,
            "completed_jobs": completed,
            "rejected_jobs": rejected,
            "max_workers": self.controller.max_workers,
            "rss_bytes": current_rss_bytes(),
            "cpu_process_ms": time.process_time() * 1000.0,
        }


APP = ReconstructionServer()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/status":
            self.send_json(200, APP.status())
            return
        if self.path == "/health":
            self.send_json(200, {"ok": True, "server": "python"})
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/reconstruct":
            self.send_json(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            model_id = payload["model_id"]
            if model_id not in CONFIG["models"]:
                raise ValueError(f"unknown model_id {model_id}")
            signal = np.array(payload["g"], dtype=np.float64)
            response = APP.enqueue_and_wait(model_id, signal)
            self.send_json(200, response)
        except TimeoutError as exc:
            self.send_json(504, {"error": str(exc)})
        except RuntimeError as exc:
            self.send_json(503, {"error": str(exc)})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

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
    host = CONFIG["server"]["python"]["host"]
    port = int(CONFIG["server"]["python"]["port"])
    APP.start_workers()
    print(f"Python CGNR server listening on http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
