# Handoff para outro agente - APS CGNR

Este documento e o ponto de partida para qualquer agente ou pessoa que assumir o trabalho. Ele junta o enunciado passado pelo usuario, as decisoes tomadas, a arquitetura implementada, os comandos de uso e os pontos de atencao.

## 1. Caminhos importantes

Pasta que aparece na Area de Trabalho do usuario:

```text
C:\Users\leand\OneDrive\Desktop\APS-CGNR
```

Outras copias sincronizadas:

```text
C:\Users\leand\Desktop\APS-CGNR
C:\Users\leand\OneDrive\Área de Trabalho\APS-CGNR
C:\Users\leand\Documents\Codex\2026-06-18\grande-ser-que-eu-consigo-rodar\work\aps-cgnr
```

Use preferencialmente:

```text
C:\Users\leand\OneDrive\Desktop\APS-CGNR
```

Python usado durante o desenvolvimento:

```text
C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
```

WSL usado para C++:

```text
Codex-Debian
```

## 2. Resumo do trabalho

O projeto pede um sistema cliente-servidor para reconstrucao de imagens usando Algebra Linear, com o algoritmo principal CGNR.

O trabalho precisa comparar duas implementacoes:

- linguagem interpretada e nao fortemente tipada: Python;
- linguagem compilada e fortemente tipada: C++.

O cliente envia sinais `g` para os servidores. Cada servidor recebe a matriz de modelo `H` e o sinal `g`, executa CGNR e devolve a imagem reconstruida. No final, o sistema gera relatorios comparando tempo, CPU, memoria, iteracoes, erro e quantidade de imagens processadas.

O professor quer avaliar:

- arquitetura cliente-servidor;
- integracao entre linguagens diferentes;
- uso de BLAS/bibliotecas de Algebra Linear;
- implementacao correta do CGNR;
- medicao de desempenho;
- controle de saturacao;
- comparacao entre interpretado e compilado;
- capacidade de reconstruir o maior numero de imagens no menor tempo possivel.

## 3. Atividades semanais do enunciado

### Atividade 1: selecao de linguagens e BLAS

Escolhas feitas:

- Python + NumPy/OpenBLAS.
- C++ + OpenBLAS.

Justificativa:

- Python atende a exigencia de linguagem interpretada e nao fortemente tipada.
- C++ atende a exigencia de linguagem compilada e fortemente tipada.
- OpenBLAS atende ao uso de biblioteca BLAS e permite comparacao honesta de algebra linear.

### Atividade 2: operacoes basicas

Operacoes exigidas:

```text
MN = M * N
aM = a * M
Ma = M * a
```

Implementacoes:

- Python: `scripts/test_basic_ops.py`
- C++: `cpp/basic_ops.cpp`

Dados usados:

```text
data/raw/Dados.zip
data/raw/Dados/M.csv
data/raw/Dados/N.csv
data/raw/Dados/MN.csv
data/raw/Dados/a.csv
data/raw/Dados/aM.csv
```

Resultado auditado:

- `MN = M * N`: OK.
- `aM = a * M`: OK, com diferenca maxima pequena por arredondamento do arquivo esperado.
- `Ma = M * a^T`: OK.
- teste escalar `a0 * M`: OK.

### Atividade 3: CGNR

Implementacoes:

- Python: `python/cgnr.py`
- C++: `cpp/cgnr.cpp`

O CGNR segue o pseudocodigo do enunciado:

```text
f0 = 0
r0 = g - H f0
z0 = H^T r0
p0 = z0

para i = 0, 1, ...
    wi = H pi
    alpha_i = ||zi||_2^2 / ||wi||_2^2
    f_{i+1} = fi + alpha_i pi
    r_{i+1} = ri - alpha_i wi
    z_{i+1} = H^T r_{i+1}
    beta_i = ||z_{i+1}||_2^2 / ||zi||_2^2
    p_{i+1} = z_{i+1} + beta_i pi
```

Criterio de parada:

```text
abs(||r_{i+1}||_2 - ||r_i||_2) < 1e-4
```

ou:

```text
iteracoes == 10
```

Observacao: o enunciado define o erro como:

```text
epsilon = ||r_{i+1}||_2 - ||r_i||_2
```

A implementacao usa o valor absoluto `abs(epsilon)` para evitar parada errada quando a diferenca for negativa.

### Atividade 4: saturacao e controle

O usuario anotou que o professor/amigo comentou:

- precisa funcionar com tres clientes simultaneos;
- nao pode quebrar;
- testar algo como 200 requisicoes por minuto;
- usar fila, threads e semaforo;
- nao fixar numero magico;
- estabelecer custo computacional;
- medir memoria e CPU;
- so disparar se houver recurso;
- threads devem ser adaptativas/flutuantes, nao um numero arbitrario fixo.

Implementacao atual:

- fila com `queue.Queue`;
- pool de workers;
- `threading.BoundedSemaphore`;
- limite de fila em `config.json`;
- estimativa de memoria por modelo;
- rejeicao controlada com erro HTTP em vez de travar;
- teste de carga fixo e adaptativo em `scripts/load_test.py`.

## 4. Formulas do enunciado

Siglas:

```text
g = vetor de sinal
H = matriz de modelo
f = imagem reconstruida
S = numero de amostras do sinal
N = numero de elementos sensores
```

Fator de reducao:

```text
c = ||H^T * H||_2
```

No projeto:

```text
reduction_factor_estimate
```

Ele e estimado por power iteration, para evitar custo absurdo de calcular exatamente a norma espectral da matriz enorme.

Coeficiente de regularizacao:

```text
lambda = max(abs(H^T * g)) * 0.10
```

No projeto:

```text
lambda
```

Observacao importante: `lambda` e calculado e reportado, mas nao altera o CGNR. Motivo: o pseudocodigo fornecido para CGNR nao aplica regularizacao dentro da iteracao.

Erro:

```text
epsilon = ||r_{i+1}||_2 - ||r_i||_2
```

No projeto:

```text
error_signed = epsilon
error_abs = abs(epsilon)
```

Ganho de sinal:

O enunciado descreve uma formula de ganho por amostra. O projeto implementa tres modos:

```text
none    = sem ganho
scalar  = ganho escalar aleatorio entre 0.85 e 1.15
formula = sqrt(100 + 0.05 * l^2)
```

Arquivo:

```text
client/compare_client.py
```

Funcao:

```text
apply_gain
```

## 5. Dados do professor

Modelo 1, imagens 60x60:

```text
H-1.csv.zip
H shape = 50816 x 3600
S = 794
N = 64
```

Sinais principais:

```text
G-1.csv
G-2.csv
A-60x60-1.csv
```

Modelo 2, imagens 30x30:

```text
H-2.csv.zip
H shape = 27904 x 900
S = 436
N = 64
```

Sinais principais:

```text
g-30x30-1.csv
g-30x30-2.csv
A-30x30-1.csv
```

Ponto de atencao:

- `A-30x30-1.csv` e `A-60x60-1.csv` possuem amplitudes muito maiores que `g/G`.
- Para comparacao mais justa, priorizar `g-30x30-*` e `G-*`.

## 6. Arquitetura implementada

### Cliente

Arquivo:

```text
client/compare_client.py
```

Responsabilidades:

- escolher sinais reais do modelo;
- aplicar ganho;
- enviar exatamente o mesmo sinal para Python e C++;
- medir tempo de ida/volta;
- gravar CSV e Markdown comparativo.

### Servidor Python

Arquivo:

```text
python/server.py
```

Endpoint:

```text
POST /reconstruct
GET /status
GET /health
```

Executa:

```text
python/cgnr.py
```

Usa:

- NumPy/OpenBLAS;
- cache de `H` em `.npy`;
- fila;
- workers;
- semaforo;
- controle de memoria;
- metrica de CPU/RSS/tempo.

### Servidor C++

Binario:

```text
build/cgnr_cpp
```

Codigo:

```text
cpp/cgnr.cpp
```

Usa:

- C++;
- OpenBLAS;
- arquivos binarios `.f64`;
- metrica de tempo/CPU/max RSS;
- saida JSON e PGM.

### Gateway C++

Arquivo:

```text
python/cpp_gateway_server.py
```

Por que existe:

- no Windows/WSL, o HTTP direto do servidor C++ dentro do WSL pode nao ficar acessivel pelo Windows;
- o gateway recebe HTTP no Windows e chama o binario C++ no WSL;
- o calculo pesado continua sendo C++ compilado com OpenBLAS.

O gateway tambem tem:

- fila;
- workers;
- semaforo;
- limite de memoria;
- endpoint `/status`.

### Dashboard

Arquivos:

```text
python/dashboard_server.py
frontend/index.html
frontend/app.js
frontend/styles.css
```

Mostra:

- status dos servidores;
- fila;
- jobs ativos;
- jobs concluidos;
- jobs rejeitados;
- workers;
- RSS;
- historico de comparacoes;
- imagens reconstruidas;
- graficos simples;
- janelas de carga/saturacao.

## 7. Estrutura de pastas

```text
APS-CGNR/
  build/
  client/
  cpp/
  data/
    raw/
  docs/
  frontend/
  outputs/
    client/
    cpp_gateway/
    load/
    python_server/
  python/
  scripts/
  config.json
  README.md
```

Arquivos mais importantes:

```text
config.json
python/cgnr.py
python/server.py
python/cpp_gateway_server.py
python/dashboard_server.py
cpp/cgnr.cpp
cpp/basic_ops.cpp
client/compare_client.py
scripts/load_test.py
scripts/test_basic_ops.py
scripts/audit_data.py
scripts/build_final_report.py
docs/manual-de-uso.md
docs/checklist-requisitos.md
```

## 8. Configuracao

Arquivo:

```text
config.json
```

Parametros principais:

```text
tolerance = 0.0001
max_iterations = 10
queue_limit = 16
request_timeout_seconds = 300
memory_soft_limit_bytes = 6000000000
python port = 8001
cpp gateway port = 8002
dashboard port = 8010
```

Carga adaptativa:

```text
default_clients = 3
initial_rate_per_minute = 60
max_rate_per_minute = 260
window_seconds = 15
max_windows = 6
step_up_factor = 1.35
step_down_factor = 0.65
max_error_rate = 0.05
max_p95_roundtrip_ms = 15000
max_avg_queue_ms = 5000
```

Esses valores existem para evitar "numeros magicos" espalhados no codigo.

## 9. Como rodar

Entre na pasta final:

```powershell
cd "C:\Users\leand\OneDrive\Desktop\APS-CGNR"
```

### Auditar dados

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\scripts\audit_data.py
```

### Testar operacoes basicas Python

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\scripts\test_basic_ops.py
```

### Compilar C++ e testar operacoes basicas

```powershell
wsl -d Codex-Debian --exec bash -lc "cd /mnt/c/Users/leand/OneDrive/Desktop/APS-CGNR/cpp && make && cd .. && ./build/basic_ops_cpp data/raw/Dados"
```

Se houver problema com acento/OneDrive no WSL, usar a copia sem OneDrive:

```powershell
cd "C:\Users\leand\Desktop\APS-CGNR"
```

e no WSL:

```powershell
wsl -d Codex-Debian --exec bash -lc "cd /mnt/c/Users/leand/Desktop/APS-CGNR/cpp && make && cd .. && ./build/basic_ops_cpp data/raw/Dados"
```

### Iniciar servidor Python

Terminal 1:

```powershell
cd "C:\Users\leand\OneDrive\Desktop\APS-CGNR"
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m python.server
```

### Iniciar gateway C++

Terminal 2:

```powershell
cd "C:\Users\leand\OneDrive\Desktop\APS-CGNR"
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m python.cpp_gateway_server
```

### Iniciar dashboard

Terminal 3:

```powershell
cd "C:\Users\leand\OneDrive\Desktop\APS-CGNR"
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m python.dashboard_server
```

Abrir no navegador:

```text
http://127.0.0.1:8010
```

### Rodar cliente comparativo

Terminal 4:

```powershell
cd "C:\Users\leand\OneDrive\Desktop\APS-CGNR"
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\client\compare_client.py --model 30x30 --count 2 --gain scalar
```

Modelo 60x60:

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\client\compare_client.py --model 60x60 --count 1 --gain none
```

### Teste com 3 clientes simultaneos

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\scripts\load_test.py --server python --model 30x30 --mode fixed --clients 3 --rate-per-minute 200 --requests 30 --gain none
```

### Teste adaptativo

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\scripts\load_test.py --server python --model 30x30 --mode adaptive --clients auto --target-rate-per-minute 200
```

### Gerar relatorio final

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\scripts\build_final_report.py
```

Saida:

```text
outputs/relatorio-final.md
```

## 10. O que ja foi validado

Validacoes executadas durante o desenvolvimento:

- Python `py_compile`: OK.
- `scripts/audit_data.py`: OK.
- `scripts/test_basic_ops.py`: OK.
- `cpp && make`: OK.
- `build/basic_ops_cpp`: OK.
- `build/cgnr_cpp` com 30x30: OK.
- Cliente comparativo 30x30 Python x C++: OK.
- Teste fixo com 3 clientes e 200 req/min planejado: OK em teste curto.
- Dashboard API: OK.

Resultados observados:

- 30x30 normalmente converge em 2 iteracoes.
- 60x60 testado e funcionando, normalmente em 3 iteracoes.
- Python e C++ produzem erros/iteracoes equivalentes.
- C++ via gateway pode ter `roundtrip_ms` maior porque cria processo/chama WSL; comparar principalmente `reconstruction_ms`.

## 11. O que esta pronto

Obrigatorio:

- cliente: pronto;
- servidor Python: pronto;
- versao C++ compilada: pronta;
- CGNR: pronto;
- operacoes basicas: prontas;
- metricas: prontas;
- relatorios: prontos;
- saturacao: pronta;
- controle com fila/threads/semaforo: pronto;
- comparacao Python x C++: pronta.

Extras:

- dashboard web;
- analise de trabalhos de referencia;
- checklist de requisitos;
- manual de uso;
- relatorio final agregado;
- teste adaptativo.

## 12. Pontos de atencao para o proximo agente

1. Nao confundir as Areas de Trabalho:

```text
C:\Users\leand\OneDrive\Desktop\APS-CGNR
C:\Users\leand\OneDrive\Área de Trabalho\APS-CGNR
C:\Users\leand\Desktop\APS-CGNR
```

A que bate com o print do usuario e:

```text
C:\Users\leand\OneDrive\Desktop\APS-CGNR
```

2. O gateway C++ e uma adaptacao por causa do Windows/WSL. O calculo pesado continua em C++/OpenBLAS.

3. CGNE nao foi implementado porque era opcional. Nao prometer CGNE sem implementar.

4. As imagens PGM possuem metadados no cabecalho E, alem disso, cada reconstrucao ja gera um PNG anotado (Pillow) com o texto desenhado de forma visivel sobre a imagem (`io_data.save_png_annotated`/`save_png_from_pgm`), salvo ao lado do PGM. Ou seja, a leitura estrita de "cada imagem devera conter os dados" ja esta atendida — nao ha mais item em aberto aqui. (A miniatura do painel e convertida do PGM, sem a legenda; o PNG com texto fica em disco.)

5. Evitar apagar arquivos grandes ou caches sem confirmar. `H-1.f64` e `H-1.npy` tem cerca de 1.3 GB cada.

6. Se OneDrive demorar para sincronizar, trabalhar na copia local:

```text
C:\Users\leand\Desktop\APS-CGNR
```

e depois copiar para:

```text
C:\Users\leand\OneDrive\Desktop\APS-CGNR
```

7. Para apresentacao, caminho mais simples:

- abrir dashboard;
- iniciar Python server e gateway C++ pelos botoes "Ligar" nos cartoes do painel (nao precisa de terminais separados);
- rodar `compare_client.py` com 30x30 (ou "Iniciar teste" no painel) e acompanhar o log ao vivo no painel "Execucao ao vivo";
- mostrar CSV/Markdown e imagens;
- rodar `load_test.py --clients 3 --rate-per-minute 200` (ou "Iniciar saturacao" no painel);
- explicar fila, workers, semaforo e controle adaptativo.

## 13. Relacao com trabalhos de referencia

O usuario passou:

```text
github.com/TKach1/image_reconstruction_rust
Trabalho dis.zip
```

Ideias aproveitadas:

- endpoint unico `/reconstruct`;
- endpoint `/status`;
- fila de processamento;
- semaforo/controle de concorrencia;
- relatorio CSV;
- teste de carga;
- cliente enviando sinais;
- separacao cliente/servidor.

Coisas que foram melhoradas/adaptadas:

- duas linguagens exigidas: Python e C++;
- uso explicito de OpenBLAS;
- mesmo sinal enviado para os dois servidores;
- relatorio comparativo unificado;
- dashboard;
- criterios do enunciado (`1e-4` e 10 iteracoes);
- calculo/report de `lambda` e `c`;
- controle de saturacao configuravel.

## 14. Como explicar para o professor

Frase curta:

```text
Implementamos um sistema cliente-servidor para reconstrucao de imagens usando CGNR. O cliente envia os mesmos sinais para dois servidores: Python/NumPy/OpenBLAS e C++/OpenBLAS. Cada servidor mede tempo, CPU, memoria, fila, erro e iteracoes. Para saturacao, usamos fila, pool de workers, semaforo e estimativa de memoria antes de aceitar requisicoes. Tambem temos um teste com tres clientes simultaneos e carga adaptativa para evitar quebra do servidor.
```

Se perguntarem sobre "numero magico":

```text
Os parametros principais ficam em config.json: tolerancia, iteracoes, limite de fila, memoria, workers, portas e politica de carga. O teste de 200 req/min e uma meta demonstrativa, nao uma constante escondida.
```

Se perguntarem sobre threads:

```text
Nao criamos uma thread por requisicao sem controle. Usamos uma fila e um conjunto limitado de workers. O semaforo limita quantas reconstrucoes podem executar ao mesmo tempo.
```

Se perguntarem sobre custo:

```text
O custo de memoria e estimado por modelo usando linhas, colunas e vetores auxiliares. O servidor pode rejeitar ou enfileirar requisicoes quando o custo estimado excede o limite configurado.
```

Se perguntarem sobre qualidade:

```text
Comparamos iteracoes, erro final, norma do residuo e imagem reconstruida. Python e C++ produzem resultados numericamente coerentes.
```
