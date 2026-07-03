# Checklist de requisitos - APS CGNR

Status auditado em 2026-06-26.

## Atividades semanais

| Requisito | Status | Onde esta |
| --- | --- | --- |
| Selecionar linguagem interpretada e biblioteca BLAS | OK | Python + NumPy/OpenBLAS, `docs/decisoes-tecnicas.md` |
| Selecionar linguagem compilada e biblioteca BLAS | OK | C++ + OpenBLAS, `cpp/Makefile` |
| Testar `MN = M * N` | OK | `scripts/test_basic_ops.py`, `cpp/basic_ops.cpp` |
| Testar `aM = a * M` | OK | `scripts/test_basic_ops.py`, `cpp/basic_ops.cpp` |
| Testar `Ma = M * a`/vetor | OK | Validado por identidade (sem `Ma.csv` de referencia) em `scripts/test_basic_ops.py` e `cpp/basic_ops.cpp` |
| Implementar CGNR | OK | `python/cgnr.py`, `cpp/cgnr.cpp` |
| Validar com dados experimentais | OK | modelos 30x30 e 60x60 em `outputs/client/` |
| Medir tempo | OK | `reconstruction_ms`, `roundtrip_ms`, `wall_ms` |
| Medir CPU | OK | `cpu_ms` em Python e C++ |
| Medir memoria | OK | RSS Python e max RSS C++ |
| Testes de saturacao | OK | `scripts/load_test.py`, `outputs/load/` (inclui serie de CPU/memoria) |
| Controle para evitar saturacao | OK | fila limitada, pool de workers derivado dos nucleos, limite de memoria estimada, rejeicao 503 |
| Medir CPU/memoria durante a saturacao | OK | `ResourceSampler` amostra `/status`; `outputs/load/load-resources-*.csv` |

## Requisitos de algoritmo

| Requisito | Status | Observacao |
| --- | --- | --- |
| Usar CGNR como algoritmo principal | OK | Implementado em Python e C++ |
| `f0 = 0` | OK | Vetor inicial zerado |
| `r0 = g - Hf0` | OK | Python usa `g - h @ f`; C++ usa `r = g` porque `f0 = 0` |
| `z0 = H^T r0` | OK | Python e C++ |
| `p0 = z0` | OK | Python e C++ |
| Calcular `w = Hp` | OK | Python `h @ p`; C++ `dgemv` |
| Calcular `alpha` | OK | `||z||^2 / ||w||^2` |
| Atualizar `f`, `r`, `z`, `beta`, `p` | OK | Python e C++ |
| Parar com erro menor que `1e-4` | OK | Usa `abs(||r(i+1)|| - ||r(i)||) < tolerance` |
| Parar em ate 10 iteracoes | OK | `max_iterations` em `config.json` |
| Calcular `lambda` | OK | Reportado como `lambda` |
| Calcular fator `c` | OK | Estimado por power iteration como `reduction_factor_estimate` |
| Aplicar regularizacao no CGNR | Nao aplicado | O pseudocodigo do enunciado nao usa `lambda` na iteracao |
| Implementar CGNE | Opcional/nao feito | CGNE nao era obrigatorio |

## Cliente

| Requisito | Status | Onde esta |
| --- | --- | --- |
| Enviar sequencia de sinais `g` | OK | `client/compare_client.py` |
| Intervalos aleatorios | OK | `time.sleep(random.uniform(...))` entre envios |
| Ganho de sinal aleatorio | OK | modo `scalar` |
| Ganho por formula | OK | modo `formula` |
| Modelo da imagem selecionavel | OK | `--model 30x30` ou `--model 60x60` |
| Mesma sequencia para duas versoes | OK | mesmo vetor enviado para Python e C++ em cada run |
| Relatorio com imagem/iteracoes/tempo | OK | `outputs/client/comparison-*.csv` e `.md` |

## Servidor

| Requisito | Status | Onde esta |
| --- | --- | --- |
| Servidor em linguagem interpretada | OK | `python/server.py` |
| Servidor em linguagem compilada | OK | binario `build/cgnr_cpp`; gateway em `python/cpp_gateway_server.py` |
| Executar reconstrucao | OK | `/reconstruct` nos dois servidores |
| Endpoint de status | OK | `/status` nos dois servidores |
| Controle de fila | OK | `queue.Queue` |
| Threads/workers | OK | pool derivado dos nucleos (`max_workers: "auto"`) |
| Evitar saturacao | OK | fila limitada, estimativa de memoria e rejeicao controlada (503/504) |

## Relatorios e apresentacao

| Requisito | Status | Onde esta |
| --- | --- | --- |
| Comparativo Python x C++ | OK | `outputs/relatorio-final.md` |
| Imagens reconstruidas | OK | `outputs/python_server/`, `outputs/cpp_gateway/` |
| Dados de iteracoes e tempo | OK | CSVs e Markdown |
| Metricas de CPU/memoria | OK | CSVs e JSONs gerados |
| Painel para acompanhar metricas | Extra/OK | `frontend/`, `python/dashboard_server.py` |
| Painel liga/desliga servidores | Extra/OK | `POST /api/server-control`; botoes nos cartoes Python/C++ |
| Painel mostra execucao ao vivo | Extra/OK | log em streaming (`stream_run`), painel "Execucao ao vivo" |
| Imagem com metadados visiveis (PNG anotado) | OK | `io_data.save_png_annotated`/`save_png_from_pgm` (Pillow); gerado ao lado do PGM |
| Teste com 3 clientes simultaneos | OK | `scripts/load_test.py --clients 3` |

## Validacoes executadas (re-rodadas em 2026-06-26, neste projeto)

- `py_compile` em todos os arquivos Python: OK.
- `scripts/test_basic_ops.py`: OK, agora com `Ma` validado por identidade (max_diff ~5e-14).
- Servidor Python no ar com `max_workers=15` (derivado de 16 nucleos; sem numero magico).
- `compare_client.py` Python 30x30 (8) e 60x60 (4): OK.
- `compare_client.py` C++ 30x30 (8) e 60x60 (3): OK.
- `compare_client.py --model random --servers python,cpp`: OK — modelo e ganho sorteados por envio, mesmo sinal aos dois servidores.
- Saturacao em rajada (Python 30x30, 60 clientes, 200 req): 61 ok / 139 rejeitadas (503), fila no pico 16, CPU pico ~3973%, RSS pico ~1662 MB.
- Saturacao adaptativa (Python 30x30): controlador decide `reduzir` quando a janela fica sobrecarregada.
- `build_final_report.py`: relatorio limpo, com coluna de memoria e filtro de procedencia (so dados deste projeto).

## Riscos/observacoes

- O gateway C++ e uma adaptacao de ambiente para Windows/WSL; o calculo pesado continua sendo C++ compilado com OpenBLAS. Como ele recarrega a matriz `H` por requisicao, o `roundtrip_ms` do C++ no 60x60 e alto — comparar principalmente `reconstruction_ms`.
- Os sinais `A-30x30-1.csv` e `A-60x60-1.csv` possuem amplitudes muito maiores que os sinais `g/G`; para comparacao justa, priorizar `g-30x30-*` e `G-*`.
- As imagens PGM carregam os metadados no cabecalho como 7 linhas `chave=valor` (`algorithm`, `language`, `job_id`, `started_at`, `ended_at`, `resolution`, `iterations`). Alem disso, cada reconstrucao gera um PNG anotado (Pillow) com o texto desenhado de forma visivel sobre a imagem — atende a leitura estrita de "cada imagem devera conter" os dados. O PNG fica salvo ao lado do PGM em `outputs/python_server/` e `outputs/cpp_gateway/`. (A miniatura exibida no painel e convertida do PGM, sem a legenda.)
- `lambda` (`0.1 * max|H^T g|`) e o fator `c` (estimativa de `||H^T H||_2`) sao calculados e reportados, mas NAO alteram a iteracao do CGNR, pois o pseudocodigo do enunciado nao aplica regularizacao. Confirmar com o enunciado se o requisito e apenas reportar `lambda` ou aplicar regularizacao efetiva (Tikhonov) — hoje o algoritmo e CGNR puro.
- Controle de saturacao: a admissao decide por fila + estimativa de memoria (nao por %CPU). O `BoundedSemaphore` e dimensionado igual ao pool de workers, entao nao e um segundo mecanismo independente — a concorrencia ja e limitada pelo numero de threads.
