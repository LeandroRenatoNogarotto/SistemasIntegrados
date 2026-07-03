from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLIENT_OUTPUTS = ROOT / "outputs" / "client"
FINAL_REPORT = ROOT / "outputs" / "relatorio-final.md"


def as_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def as_int(value: str | None) -> int | None:
    number = as_float(value)
    return int(number) if number is not None else None


def mean(values: list[float | int | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return statistics.mean(clean) if clean else None


def fmt(value: float | int | None, digits: int = 3) -> str:
    if value is None:
        return ""
    return f"{float(value):.{digits}f}"


def under_root(image_path: str | None) -> bool:
    """True se a imagem foi gerada DENTRO deste projeto.

    Evita misturar no relatorio dados importados de outra maquina/checkout
    (ex.: copias antigas em Documents\\Codex), que inflariam as medias e
    apareceriam como se fossem resultados deste projeto.
    """
    if not image_path:
        return False
    try:
        Path(image_path).resolve().relative_to(ROOT)
        return True
    except (ValueError, OSError):
        return False


def row_rss_mb(row: dict[str, str]) -> float | None:
    """RSS da linha em MB, normalizando bytes (Python) e KB (C++)."""
    rss_bytes = as_float(row.get("rss_end_bytes"))
    if rss_bytes is not None:
        return rss_bytes / (1024 * 1024)
    rss_kb = as_float(row.get("rss_end_kb"))
    if rss_kb is not None:
        return rss_kb / 1024
    return None


def load_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted(CLIENT_OUTPUTS.glob("comparison-*.csv")):
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                row["_source"] = path.name
                rows.append(row)
    return rows


def main() -> None:
    rows = load_rows()
    all_ok = [row for row in rows if row.get("status") == "ok"]
    # Filtro de procedencia: so entram resultados gerados neste projeto.
    ok_rows = [row for row in all_ok if under_root(row.get("image_path"))]
    discarded = len(all_ok) - len(ok_rows)
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in ok_rows:
        groups[(row.get("model_id", ""), row.get("server", ""))].append(row)

    FINAL_REPORT.parent.mkdir(parents=True, exist_ok=True)
    with FINAL_REPORT.open("w", encoding="utf-8") as f:
        f.write("# Relatorio final comparativo - CGNR\n\n")
        f.write(f"Gerado em: {datetime.now().isoformat(timespec='seconds')}\n\n")

        f.write("## Escopo validado\n\n")
        f.write("- Cliente envia os mesmos sinais para os dois servidores.\n")
        f.write("- Servidor Python executa CGNR com NumPy/OpenBLAS.\n")
        f.write("- Versao C++ executa CGNR em binario compilado com OpenBLAS no WSL.\n")
        f.write("- Modelos `30x30` e `60x60` foram testados com os dados reais.\n")
        f.write("- Relatorios e imagens sao gerados em `outputs/`.\n\n")

        if discarded:
            f.write(
                f"> Nota de procedencia: {discarded} linha(s) `ok` foram ignoradas por terem "
                f"sido geradas fora deste projeto (imagens fora de `{ROOT.name}`).\n\n"
            )

        f.write("## Medias por modelo e servidor\n\n")
        f.write("| Modelo | Servidor | Amostras | Iteracoes medias | Reconstrucao media ms | Roundtrip medio ms | CPU media ms | Memoria media MB | Erro medio |\n")
        f.write("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |\n")
        for (model_id, server), items in sorted(groups.items()):
            f.write(
                "| {model} | {server} | {count} | {iters} | {recon} | {roundtrip} | {cpu} | {mem} | {error} |\n".format(
                    model=model_id,
                    server=server,
                    count=len(items),
                    iters=fmt(mean([as_int(row.get("iterations")) for row in items]), 2),
                    recon=fmt(mean([as_float(row.get("reconstruction_ms")) for row in items])),
                    roundtrip=fmt(mean([as_float(row.get("roundtrip_ms")) for row in items])),
                    cpu=fmt(mean([as_float(row.get("cpu_ms")) for row in items])),
                    mem=fmt(mean([row_rss_mb(row) for row in items]), 1),
                    error=fmt(mean([as_float(row.get("error_abs")) for row in items]), 8),
                )
            )

        f.write("\n## Ultimas imagens geradas\n\n")
        for row in ok_rows[-8:]:
            f.write(f"- `{row.get('server')}` `{row.get('model_id')}`: `{row.get('image_path')}`\n")

        f.write("\n## Observacoes tecnicas\n\n")
        f.write("- O tempo `reconstruction_ms` mede o nucleo de reconstrucao CGNR.\n")
        f.write("- A coluna de memoria usa o RSS reportado por cada servidor: no Python e o WorkingSet do processo inteiro (inclui a matriz H em cache); no C++ e o pico de RSS (max_rss) do processo da requisicao. Sao escopos diferentes e devem ser lidos como ordens de grandeza, nao comparacao direta byte a byte.\n")
        f.write("- O tempo `roundtrip_ms` inclui serializacao, transferencia, fila e overhead do gateway.\n")
        f.write("- No C++, o gateway HTTP chama o binario compilado no WSL. Isso preserva a execucao compilada, mas aumenta o roundtrip porque o processo e a matriz podem ser carregados por requisicao.\n")
        f.write("- Para uma versao final mais performatica, o ideal e expor o servidor C++ persistente diretamente ou configurar encaminhamento de porta Windows -> WSL.\n")
        f.write("- `A-30x30-1.csv` e `A-60x60-1.csv` foram mantidos como dados disponiveis, mas os testes comparativos usam prioritariamente `g/G` por terem escala de sinal consistente.\n")

    print(FINAL_REPORT)


if __name__ == "__main__":
    main()
