from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from python.io_data import load_h_from_zip, read_vector

from common import CONFIG, ROOT, resolve_project_path


def stream_h_to_binary_and_npy(zip_path: Path, csv_name: str, rows: int, cols: int, h_bin: Path, cache_path: Path) -> None:
    h_bin.parent.mkdir(parents=True, exist_ok=True)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    memmap = np.lib.format.open_memmap(cache_path, mode="w+", dtype=np.float64, shape=(rows, cols))

    with zipfile.ZipFile(zip_path) as zf, zf.open(csv_name) as raw, h_bin.open("wb") as binary:
        for row_index, line in enumerate(raw):
            if row_index >= rows:
                raise ValueError(f"H has more rows than expected ({rows})")
            values = np.fromstring(line.decode("utf-8").strip(), sep=",", dtype=np.float64)
            if values.shape[0] != cols:
                raise ValueError(f"row {row_index} has {values.shape[0]} columns; expected {cols}")
            memmap[row_index, :] = values
            values.tofile(binary)
            if (row_index + 1) % 5000 == 0:
                print(f"  {row_index + 1}/{rows} linhas convertidas...")

    if row_index + 1 != rows:
        raise ValueError(f"H has {row_index + 1} rows; expected {rows}")
    memmap.flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="30x30", choices=CONFIG["models"].keys())
    parser.add_argument("--streaming", action="store_true", help="converte H linha a linha para reduzir pico de memoria")
    args = parser.parse_args()

    model = CONFIG["models"][args.model]
    h_csv = model["h_csv"]
    h_zip = resolve_project_path(model["h_zip"])
    cache_path = ROOT / "data" / f"{Path(h_csv).stem}.npy"
    h_bin = ROOT / "data" / f"{Path(h_csv).stem}.f64"
    rows = int(model["rows"])
    cols = int(model["cols"])

    if args.model == "60x60" or args.streaming:
        print(f"Convertendo H por streaming: {h_zip.name} -> {h_bin.name} / {cache_path.name}")
        stream_h_to_binary_and_npy(h_zip, h_csv, rows, cols, h_bin, cache_path)
        h_shape = (rows, cols)
    else:
        h = load_h_from_zip(h_zip, h_csv, cache_path)
        h.astype("float64").tofile(h_bin)
        h_shape = h.shape
    print(f"H binario: {h_bin} shape={h_shape}")

    signal_outputs = []
    for signal in model["signals"]:
        signal_path = resolve_project_path(signal)
        values = read_vector(signal_path)
        out = ROOT / "data" / f"{signal_path.stem}.f64"
        values.astype("float64").tofile(out)
        signal_outputs.append(str(out))
        print(f"Sinal binario: {out} shape={values.shape}")

    metadata = {
        "model": args.model,
        "width": model["width"],
        "height": model["height"],
        "rows": int(h_shape[0]),
        "cols": int(h_shape[1]),
        "h_bin": str(h_bin),
        "signals": signal_outputs,
    }
    metadata_path = ROOT / "data" / f"{Path(h_csv).stem}.metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Metadata: {metadata_path}")


if __name__ == "__main__":
    main()
