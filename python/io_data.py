from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np


def read_vector(path: Path) -> np.ndarray:
    return np.loadtxt(path, dtype=np.float64)


def load_h_from_zip(zip_path: Path, csv_name: str, cache_path: Path | None = None) -> np.ndarray:
    if cache_path and cache_path.exists():
        return np.load(cache_path, mmap_mode=None)

    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(csv_name) as f:
            matrix = np.loadtxt(f, delimiter=",", dtype=np.float64)

    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache_path, matrix)

    return matrix


def normalize_to_uint8(image: np.ndarray, width: int, height: int) -> np.ndarray:
    values = image.astype(np.float64)
    lo = float(values.min())
    hi = float(values.max())
    if abs(hi - lo) < 1e-15:
        pixels = np.zeros_like(values, dtype=np.uint8)
    else:
        pixels = np.clip((values - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)
    return pixels.reshape((height, width), order="F")


def save_pgm(path: Path, image: np.ndarray, width: int, height: int, comments: list[str]) -> None:
    pixels = normalize_to_uint8(image, width, height)
    with path.open("wb") as f:
        f.write(b"P5\n")
        for comment in comments:
            f.write(f"# {comment}\n".encode("utf-8"))
        f.write(f"{width} {height}\n255\n".encode("ascii"))
        f.write(pixels.tobytes())


def _annotate_and_save(gray: "object", path: Path, caption_lines: list[str]) -> None:
    """Escala a imagem em tons de cinza e desenha os metadados visiveis abaixo.

    Atende a leitura estrita do requisito "cada imagem devera conter" os dados
    (algoritmo, inicio, fim, tamanho, iteracoes) como TEXTO VISIVEL na imagem,
    alem dos metadados ja gravados no cabecalho do PGM.
    """
    from PIL import Image, ImageDraw

    base = gray.convert("L")
    w, h = base.size
    scale = max(1, round(360 / max(w, h)))
    base = base.resize((w * scale, h * scale), Image.NEAREST).convert("RGB")
    sw, sh = base.size

    line_height = 16
    pad = 8
    caption_h = pad * 2 + line_height * len(caption_lines)
    canvas = Image.new("RGB", (max(sw, 360), sh + caption_h), (255, 255, 255))
    canvas.paste(base, (0, 0))
    draw = ImageDraw.Draw(canvas)
    y = sh + pad
    for line in caption_lines:
        draw.text((pad, y), line, fill=(0, 0, 0))
        y += line_height
    canvas.save(path, format="PNG")


def save_png_annotated(path: Path, image: np.ndarray, width: int, height: int, caption_lines: list[str]) -> None:
    from PIL import Image

    pixels = normalize_to_uint8(image, width, height)
    gray = Image.fromarray(pixels, mode="L")
    _annotate_and_save(gray, path, caption_lines)


def save_png_from_pgm(path: Path, pgm_path: Path, caption_lines: list[str]) -> None:
    from PIL import Image

    with Image.open(pgm_path) as gray:
        gray.load()
        _annotate_and_save(gray, path, caption_lines)
