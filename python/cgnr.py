from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from .metrics import MetricTimer


@dataclass
class CgnrResult:
    image: np.ndarray
    iterations: int
    error_abs: float
    error_signed: float
    residual_norm: float
    lambda_value: float
    reduction_factor_estimate: float
    started_at: datetime
    ended_at: datetime
    metrics: dict[str, float | int]


def estimate_reduction_factor(h: np.ndarray, rounds: int = 8) -> float:
    n = h.shape[1]
    v = np.ones(n, dtype=np.float64) / np.sqrt(n)
    estimate = 0.0
    for _ in range(rounds):
        w = h.T @ (h @ v)
        estimate = float(np.linalg.norm(w, ord=2))
        if estimate == 0.0:
            return 0.0
        v = w / estimate
    return estimate


def cgnr(h: np.ndarray, g: np.ndarray, max_iterations: int = 10, tolerance: float = 1e-4) -> CgnrResult:
    started_at = datetime.now()
    timer = MetricTimer.start()

    f = np.zeros(h.shape[1], dtype=np.float64)
    r = g - h @ f
    z = h.T @ r
    p = z.copy()

    lambda_value = float(np.max(np.abs(z)) * 0.10)
    reduction_factor = estimate_reduction_factor(h)

    previous_norm = float(np.linalg.norm(r, ord=2))
    error_signed = previous_norm
    error_abs = abs(error_signed)
    iterations = 0

    for i in range(max_iterations):
        w = h @ p
        z_norm_sq = float(z @ z)
        w_norm_sq = float(w @ w)
        if w_norm_sq == 0.0 or z_norm_sq == 0.0:
            break

        alpha = z_norm_sq / w_norm_sq
        f = f + alpha * p
        r = r - alpha * w
        z_next = h.T @ r

        current_norm = float(np.linalg.norm(r, ord=2))
        error_signed = current_norm - previous_norm
        error_abs = abs(error_signed)
        iterations = i + 1

        z_next_norm_sq = float(z_next @ z_next)
        beta = z_next_norm_sq / z_norm_sq
        p = z_next + beta * p
        z = z_next
        previous_norm = current_norm

        if error_abs < tolerance:
            break

    ended_at = datetime.now()
    metrics = timer.stop()
    return CgnrResult(
        image=f,
        iterations=iterations,
        error_abs=error_abs,
        error_signed=error_signed,
        residual_norm=previous_norm,
        lambda_value=lambda_value,
        reduction_factor_estimate=reduction_factor,
        started_at=started_at,
        ended_at=ended_at,
        metrics=metrics,
    )
