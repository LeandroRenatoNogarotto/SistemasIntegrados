from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def main() -> None:
    signal = np.loadtxt((ROOT / "data/raw/g-30x30-1.csv").resolve(), dtype=np.float64)
    payload = json.dumps({"model_id": "30x30", "g": signal.tolist()}).encode("utf-8")
    req = Request(
        "http://127.0.0.1:8001/reconstruct",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=300) as response:
        print(response.read().decode("utf-8"))


if __name__ == "__main__":
    sys.exit(main())
