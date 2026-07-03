# Manual de uso - APS CGNR

Este manual descreve como executar o projeto de reconstrucao de imagens por CGNR, comparar as versoes Python e C++ e gerar metricas de desempenho/saturacao.

## 1. O que foi implementado

- Servidor interpretado: Python + NumPy/OpenBLAS.
- Servidor compilado: C++ + OpenBLAS.
- Gateway HTTP para chamar o binario C++ em ambiente Windows/WSL.
- Cliente comparativo que envia o mesmo sinal para os dois servidores.
- Algoritmo CGNR com limite de 10 iteracoes e tolerancia `1e-4`.
- Calculo/report de `lambda = 0.1 * max(abs(H^Tg))`.
- Estimativa do fator de reducao `c = ||H^T H||_2` por power iteration.
- Metricas de tempo, CPU, memoria, fila, iteracoes, erro e norma do residuo.
- Imagens PGM reconstruidas com metadados no cabecalho (7 linhas `chave=valor`).
- Imagens PNG anotadas com os metadados desenhados de forma visivel (algoritmo, inicio, termino, tamanho, iteracoes), geradas ao lado do PGM via Pillow.
- Relatorios CSV/Markdown para comparacao.
- Teste de saturacao fixo e adaptativo, com amostragem de CPU/memoria (`ResourceSampler`).
- Controle de saturacao com fila limitada, pool de workers derivado dos nucleos e rejeicao controlada (503/504) por estimativa de memoria.
- Dashboard web para acompanhar servidores, metricas, relatorios, imagens e testes de carga, LIGAR/DESLIGAR os servidores e acompanhar cada execucao ao vivo (log em streaming).

## 2. Estrutura principal

- `config.json`: parametros do sistema, modelos, portas, limites e carga adaptativa.
- `python/`: implementacao Python, servidores HTTP, gateway C++ e dashboard.
- `cpp/`: implementacao C++ com OpenBLAS e Makefile.
- `client/compare_client.py`: cliente comparativo Python x C++.
- `scripts/`: auditoria, preparo de dados, operacoes basicas, carga e relatorios.
- `frontend/`: painel web.
- `data/raw/`: dados originais.
- `data/`: dados binarios/cache para execucao rapida.
- `outputs/`: imagens, CSVs, Markdown e relatorios gerados.
- `docs/`: decisoes tecnicas, metricas, checklist e este manual.

## 3. Requisitos de ambiente

No Windows:

- Python do runtime do Codex:

```powershell
C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
```

- WSL `Codex-Debian` com `g++`, `make`, `pkg-config` e `libopenblas-dev`.

O projeto final esta em:

```text
C:\Users\leand\OneDrive\Desktop\APS-CGNR
```

## 4. Preparar dados

Entre na pasta:

```powershell
cd C:\Users\leand\OneDrive\Desktop\APS-CGNR
```

Auditar arquivos:

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\scripts\audit_data.py
```

Gerar binarios/cache, se necessario:

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\scripts\prepare_binary_data.py --model 30x30
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\scripts\prepare_binary_data.py --model 60x60 --streaming
```

## 5. Testar operacoes basicas

Python/NumPy:

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\scripts\test_basic_ops.py
```

C++/OpenBLAS:

```powershell
wsl -d Codex-Debian --exec bash -lc "cd /mnt/c/Users/leand/OneDrive/Desktop/APS-CGNR/cpp && make && cd .. && ./build/basic_ops_cpp data/raw/Dados"
```

## 6. Iniciar os servidores

Terminal 1, servidor Python:

```powershell
cd C:\Users\leand\OneDrive\Desktop\APS-CGNR
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m python.server
```

Terminal 2, gateway C++:

```powershell
cd C:\Users\leand\OneDrive\Desktop\APS-CGNR
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m python.cpp_gateway_server
```

Terminal 3, dashboard:

```powershell
cd C:\Users\leand\OneDrive\Desktop\APS-CGNR
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m python.dashboard_server
```

Abra:

```text
http://127.0.0.1:8010
```

Endpoints uteis:

- `http://127.0.0.1:8001/status`: estado do servidor Python.
- `http://127.0.0.1:8002/status`: estado do gateway C++.
- `http://127.0.0.1:8010/api/summary`: resumo lido pelo dashboard.

### 6.1 Ligar/desligar os servidores pelo painel

Basta subir o dashboard (Terminal 3 acima). Os servidores Python e C++ podem ser
ligados e desligados direto pela pagina, sem abrir os terminais 1 e 2:

- Em cada cartao (Python e C++) ha um botao que reflete o estado real:
  - `Ligar` quando o servidor esta offline (sobe `python -m python.server` ou
    `python -m python.cpp_gateway_server`);
  - `Desligar` quando esta online e foi iniciado pelo proprio painel (encerra o
    processo e a arvore de subprocessos, inclusive o `wsl.exe`/`cgnr_cpp` do C++);
  - `Externo` (desabilitado) quando o servidor foi iniciado fora do painel — nesse
    caso nao ha processo para encerrar aqui.
- Se um servidor falhar ao subir, o final do log de inicializacao aparece no proprio
  cartao para diagnostico.

Por baixo, isso usa `POST /api/server-control` com `{ "action": "start"|"stop", "target": "python"|"cpp" }`.

### 6.2 Acompanhar a execucao ao vivo

O painel "Execucao ao vivo" transmite o log linha a linha enquanto um teste roda
(cada reconstrucao aparece na hora, em vez de so o resultado no final). Vale tanto
para o comparativo ("Iniciar teste") quanto para a saturacao ("Iniciar saturacao").

## 7. Rodar comparacao Python x C++

Modelo 30x30:

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\client\compare_client.py --model 30x30 --count 2 --gain scalar
```

Modelo 60x60:

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\client\compare_client.py --model 60x60 --count 1 --gain none
```

Saidas:

- `outputs/client/comparison-*.csv`
- `outputs/client/comparison-*.md`
- imagens em `outputs/python_server/` e `outputs/cpp_gateway/`

## 8. Rodar teste com 3 clientes simultaneos

Teste fixo, bom para demonstracao:

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\scripts\load_test.py --server python --model 30x30 --mode fixed --clients 3 --rate-per-minute 200 --requests 30 --gain none
```

Interpretacao:

- `--clients 3`: simula tres clientes simultaneos.
- `--rate-per-minute 200`: tenta enviar 200 requisicoes por minuto.
- `--requests 30`: total de requisicoes planejadas.

## 9. Rodar teste adaptativo

O modo adaptativo aumenta ou reduz a taxa automaticamente conforme o servidor aguenta:

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\scripts\load_test.py --server python --model 30x30 --mode adaptive --clients auto --target-rate-per-minute 200
```

Ele mede por janela:

- taxa de erro;
- latencia p95;
- tempo medio em fila;
- vazao atingida;
- decisao de aumentar, reduzir ou manter.

Saidas:

- `outputs/load/load-*.csv`: requisicoes individuais.
- `outputs/load/load-windows-*.csv`: resumo por janela.
- `outputs/load/load-*.md`: relatorio do teste.

## 10. Gerar relatorio final agregado

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\scripts\build_final_report.py
```

Saida:

```text
outputs/relatorio-final.md
```

## 11. Como explicar na apresentacao

Resumo curto:

1. O cliente escolhe sinais reais dos dados do professor.
2. Aplica ganho aleatorio ou formula de ganho.
3. Envia exatamente o mesmo vetor `g` para Python e C++.
4. Cada servidor executa CGNR sobre a matriz `H`.
5. O servidor para quando `erro < 1e-4` ou quando chega a 10 iteracoes.
6. Cada resposta traz imagem, iteracoes, tempo, CPU, memoria e erro.
7. O cliente gera relatorio comparativo.
8. O teste de saturacao simula varios clientes e mostra que o sistema nao quebra porque usa fila limitada, pool de workers derivado dos nucleos e rejeicao controlada (503/504) baseada em fila + estimativa de memoria.

## 12. Observacoes importantes

- CGNE nao foi implementado porque era opcional; CGNR e o algoritmo principal do enunciado.
- `lambda` e `c` sao calculados/reportados, mas nao alteram o CGNR, porque o pseudocodigo fornecido nao aplica regularizacao na iteracao.
- A versao C++ roda como binario compilado com OpenBLAS. O gateway Python existe apenas para adaptar HTTP no Windows/WSL.
- As imagens PGM possuem metadados no cabecalho (7 linhas `chave=valor`: algorithm, language, job_id, started_at, ended_at, resolution, iterations). Ao lado de cada PGM tambem e salvo um PNG anotado com esses dados desenhados de forma visivel. O dashboard e os relatorios tambem exibem as informacoes de execucao. Observacao: a miniatura mostrada no proprio painel e convertida do PGM (sem a legenda); o PNG anotado com texto fica salvo em disco, em `outputs/python_server/` e `outputs/cpp_gateway/`.
