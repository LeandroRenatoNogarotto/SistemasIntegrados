from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = (ROOT / "data/raw").resolve()
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def resolve_project_path(path: str) -> Path:
    return (ROOT / path).resolve()


def read_matrix_semicolon(path: Path) -> np.ndarray:
    rows: list[list[float]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=";")
        for row in reader:
            if row:
                rows.append([float(value) for value in row])
    return np.array(rows, dtype=np.float64)


def read_vector_lines(path: Path) -> np.ndarray:
    values: list[float] = []
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                values.append(float(stripped.replace(",", ".")))
    return np.array(values, dtype=np.float64)


def read_h_preview(zip_path: Path, csv_name: str, max_rows: int = 3) -> list[str]:
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(csv_name) as raw:
            lines: list[str] = []
            for _ in range(max_rows):
                line = raw.readline()
                if not line:
                    break
                lines.append(line.decode("utf-8").strip())
            return lines


def count_h_rows(zip_path: Path, csv_name: str) -> int:
    count = 0
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(csv_name) as raw:
            for _ in raw:
                count += 1
    return count


def count_csv_columns(line: str) -> int:
    if not line:
        return 0
    return len(line.split(","))
