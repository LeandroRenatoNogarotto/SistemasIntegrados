from __future__ import annotations

from pathlib import Path

import numpy as np

from common import DATA_ROOT, read_matrix_semicolon


def assert_close(name: str, actual: np.ndarray, expected: np.ndarray, atol: float = 1e-9) -> None:
    if actual.shape != expected.shape:
        raise AssertionError(f"{name}: shape {actual.shape} != {expected.shape}")
    diff = float(np.max(np.abs(actual - expected)))
    if not np.allclose(actual, expected, atol=atol):
        raise AssertionError(f"{name}: max diff {diff}")
    print(f"OK {name}: shape={actual.shape} max_diff={diff:.6g}")


def main() -> None:
    base = DATA_ROOT / "Dados"
    if not base.exists():
        raise SystemExit(f"Extraia Dados.zip para {base}")

    m = read_matrix_semicolon(base / "M.csv")
    n = read_matrix_semicolon(base / "N.csv")
    mn_expected = read_matrix_semicolon(base / "MN.csv")
    a = read_matrix_semicolon(base / "a.csv")
    am_expected = read_matrix_semicolon(base / "aM.csv")

    mn = m @ n
    assert_close("MN = M * N", mn, mn_expected)

    # O arquivo a.csv contem uma linha com 10 escalares. No conjunto de teste,
    # aM.csv corresponde ao produto matricial a * M.
    am = a @ m
    assert_close("aM = a * M", am, am_expected, atol=5e-3)

    # Ma = M * a (matriz por vetor). O conjunto Dados nao traz um arquivo de
    # referencia para Ma, entao validamos contra uma identidade auto-verificavel:
    # cada elemento de M @ a^T deve ser igual ao produto interno da linha de M
    # pelo vetor a, calculado de forma independente.
    ma = m @ a.T
    ma_manual = np.array(
        [[float(np.dot(m[i, :], a[0, :]))] for i in range(m.shape[0])],
        dtype=np.float64,
    )
    assert_close("Ma = M * a^T", ma, ma_manual)

    scalar = float(a[0, 0])
    scalar_left = scalar * m
    scalar_right = m * scalar
    assert_close("a0M = a0 * M", scalar_left, scalar_right)


if __name__ == "__main__":
    main()
