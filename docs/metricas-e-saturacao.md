# Metricas e controle de saturacao

## Metricas por requisicao

Cada requisicao deve registrar:

- identificador da requisicao;
- modelo (`30x30` ou `60x60`);
- algoritmo (`CGNR` e opcionalmente `CGNE`);
- linguagem/servidor (`Python` ou `C++`);
- horario de chegada;
- horario de inicio do processamento;
- horario de termino;
- tempo em fila;
- tempo de reconstrucao;
- tempo total;
- iteracoes;
- erro final;
- norma final do residuo;
- memoria antes/depois ou pico;
- tempo de CPU;
- status (`processed`, `queued`, `rejected`, `failed`).

## Custo estimado

Antes de processar, o servidor deve estimar:

- bytes da matriz `H`: `linhas * colunas * sizeof(double)`;
- bytes dos vetores principais: `g`, `f`, `r`, `z`, `p`, `w`;
- custo por iteracao: uma multiplicacao `H*p` e uma `H^T*r`;
- tempo medio historico do mesmo modelo.

## Controle adaptativo

A politica inicial:

- manter uma fila de requisicoes;
- detectar o numero de nucleos/threads disponiveis;
- nao fixar manualmente o numero de threads (workers derivados dos nucleos, ver `max_workers: "auto"`);
- limitar a concorrencia real pelo tamanho do pool de workers;
- aceitar nova tarefa somente se o custo de memoria estimado couber no limite configurado;
- caso contrario, manter em fila ou rejeitar com resposta explicita.

## Implementacao atual do controle

Os servidores usam os seguintes mecanismos:

- `queue.Queue` (limitada) para absorver rajadas de requisicoes;
- pool de workers derivado dos nucleos da maquina (`max_workers: "auto"`), que e o que de fato limita a concorrencia da reconstrucao;
- estimativa de memoria por modelo antes de aceitar o trabalho (admissao por fila + memoria);
- limite maximo de fila em `config.json`;
- resposta `503` ou `504` quando o servidor esta saturado, sem encerrar o processo.

Observacao honesta: existe um `BoundedSemaphore` no codigo, mas como ele e dimensionado igual ao numero de workers, quem realmente limita a concorrencia e o tamanho do pool; o semaforo nao e um segundo mecanismo independente. A admissao NAO usa percentual de CPU; ela decide por tamanho de fila e memoria estimada.

O gateway C++ usa os mesmos mecanismos de fila e pool de workers. Mesmo chamando o binario C++ pelo WSL, o controle de entrada fica no gateway HTTP, impedindo que varias chamadas concorrentes criem processos sem limite.

## Teste de carga dinamico

O script `scripts/load_test.py` possui dois modos:

- `adaptive`: modo padrao. A carga sobe ou desce por janelas de avaliacao.
- `fixed`: modo reprodutivel para testar uma taxa especifica.

No modo adaptativo, cada janela mede:

- taxa de erro;
- latencia p95;
- tempo medio em fila;
- quantidade de requisicoes processadas;
- taxa planejada e taxa efetivamente alcancada.

Em paralelo, um coletor (`ResourceSampler`) amostra o endpoint `/status` de cada servidor durante todo o teste e registra memoria (RSS), profundidade da fila, jobs ativos/rejeitados e uso de CPU (%), atendendo a exigencia de "medir memoria e CPU" na saturacao.

Se a janela estiver saudavel, o cliente aumenta a taxa. Se houver erro, fila alta ou p95 alto, a taxa e reduzida. Assim, `200 req/min` pode ser usado como meta de demonstracao, mas nao fica preso como numero fixo no codigo.

Comando recomendado para demonstracao:

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\scripts\load_test.py --server python --model 30x30 --mode adaptive --clients auto --target-rate-per-minute 200
```

Saidas geradas:

- `outputs/load/load-*.csv`: requisicoes individuais (inclui `cpu_ms` e RSS por requisicao);
- `outputs/load/load-windows-*.csv`: resumo por janela;
- `outputs/load/load-resources-*.csv`: serie temporal de CPU/memoria/fila por servidor;
- `outputs/load/load-*.md`: relatorio narrativo do teste, com tabela de recursos.

## Sem numeros magicos

Ficam em configuracao (`config.json`):

- tolerancia;
- maximo de iteracoes;
- limite maximo de fila;
- limite de memoria (`memory_soft_limit_bytes`, em bytes absolutos);
- politica de concorrencia (`max_workers: "auto"`, derivado dos nucleos);
- modelos habilitados.

Esses parametros ficam em `config.json`, incluindo a secao `load_test` para janelas, fatores de aumento/reducao e limites de saude do teste. Observacao: a admissao usa um limite de memoria em bytes (nao um percentual) e nao usa um limite de CPU; a CPU e medida e reportada, mas nao e usada como criterio de admissao.
