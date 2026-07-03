from __future__ import annotations

import zipfile

from common import CONFIG, DATA_ROOT, count_csv_columns, count_h_rows, read_h_preview, read_vector_lines, resolve_project_path


def main() -> None:
    print("DATA_ROOT", DATA_ROOT)
    print()

    dados_zip = DATA_ROOT / "Dados.zip"
    print("Dados.zip")
    with zipfile.ZipFile(dados_zip) as zf:
        for info in zf.infolist():
            print(f"  {info.filename:8s} {info.file_size:8d} bytes")
    print()

    for model_name, model in CONFIG["models"].items():
        width = model["width"]
        height = model["height"]
        expected_cols = width * height
        h_zip = resolve_project_path(model["h_zip"])
        h_csv = model["h_csv"]
        preview = read_h_preview(h_zip, h_csv, max_rows=1)
        cols = count_csv_columns(preview[0])
        rows = count_h_rows(h_zip, h_csv)

        print(f"Modelo {model_name}")
        print(f"  H: {rows} x {cols}")
        print(f"  colunas esperadas por pixels: {expected_cols}")
        print(f"  zip: {h_zip.name}")
        for signal in model["signals"]:
            path = resolve_project_path(signal)
            values = read_vector_lines(path)
            print(f"  sinal {path.name}: {values.shape[0]} valores min={values.min():.6g} max={values.max():.6g}")
        print()


if __name__ == "__main__":
    main()
