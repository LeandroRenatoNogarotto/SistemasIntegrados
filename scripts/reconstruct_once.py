from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from python.cgnr import cgnr
from python.io_data import load_h_from_zip, read_vector, save_pgm

from common import CONFIG, ROOT, resolve_project_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="30x30", choices=CONFIG["models"].keys())
    parser.add_argument("--signal", default=None)
    args = parser.parse_args()

    model = CONFIG["models"][args.model]
    width = int(model["width"])
    height = int(model["height"])
    signal_path = resolve_project_path(args.signal or model["signals"][0])
    h_zip = resolve_project_path(model["h_zip"])
    h_csv = model["h_csv"]
    cache_path = ROOT / "data" / f"{Path(h_csv).stem}.npy"

    print(f"Carregando H de {h_zip.name} ({args.model})")
    h = load_h_from_zip(h_zip, h_csv, cache_path)
    print(f"H shape={h.shape} dtype={h.dtype}")

    g = read_vector(signal_path)
    print(f"g={signal_path.name} shape={g.shape}")

    result = cgnr(
        h,
        g,
        max_iterations=int(CONFIG["max_iterations"]),
        tolerance=float(CONFIG["tolerance"]),
    )

    output_image = ROOT / "outputs" / f"reconstruction-{args.model}-{signal_path.stem}.pgm"
    save_pgm(
        output_image,
        result.image,
        width,
        height,
        [
            "algorithm=CGNR",
            f"started_at={result.started_at.isoformat(timespec='seconds')}",
            f"ended_at={result.ended_at.isoformat(timespec='seconds')}",
            f"resolution={width}x{height}",
            f"iterations={result.iterations}",
        ],
    )

    report = {
        "model": args.model,
        "signal": signal_path.name,
        "h_shape": list(h.shape),
        "algorithm": "CGNR",
        "iterations": result.iterations,
        "error_abs": result.error_abs,
        "error_signed": result.error_signed,
        "residual_norm": result.residual_norm,
        "lambda": result.lambda_value,
        "reduction_factor_estimate": result.reduction_factor_estimate,
        "started_at": result.started_at.isoformat(timespec="seconds"),
        "ended_at": result.ended_at.isoformat(timespec="seconds"),
        "metrics": result.metrics,
        "image": str(output_image),
    }
    report_path = ROOT / "outputs" / f"reconstruction-{args.model}-{signal_path.stem}.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
