# Decisoes tecnicas

## Linguagens

### Python

Escolha para a versao interpretada e nao fortemente tipada.

Biblioteca BLAS: NumPy usando OpenBLAS.

Motivos:

- implementacao rapida do prototipo;
- operacoes matriciais otimizadas;
- boa capacidade de medicao e geracao de relatorio.

### C++

Escolha preferencial para a versao compilada e fortemente tipada.

Biblioteca candidata:

- OpenBLAS, se o ambiente permitir instalacao/compilacao;
- Eigen como alternativa header-only, caso OpenBLAS complique o ambiente.

Motivos:

- linguagem compilada classica para alto desempenho;
- controle maior de memoria e threads;
- melhor aderencia ao que o professor comentou sobre custo computacional.

## Numeros magicos

Valores como tolerancia, limite de iteracoes, modelo usado e politicas de saturacao devem ficar em configuracao.

Valores iniciais do enunciado:

- tolerancia: `1e-4`;
- iteracoes maximas: `10`;
- algoritmos: `CGNR` como principal, `CGNE` opcional.

## CGNR

O pseudocodigo do enunciado deve ser seguido literalmente na implementacao principal:

1. `f0 = 0`
2. `r0 = g - H f0`
3. `z0 = H^T r0`
4. `p0 = z0`
5. em cada iteracao:
   - `w = H p`
   - `alpha = ||z||^2 / ||w||^2`
   - `f = f + alpha p`
   - `r = r - alpha w`
   - `z_next = H^T r`
   - `beta = ||z_next||^2 / ||z||^2`
   - `p = z_next + beta p`

## Lambda e fator de reducao

O enunciado pede calcular:

- `c = ||H^T H||_2`
- `lambda = max(abs(H^T g)) * 0.10`

Como o pseudocodigo do CGNR fornecido nao usa `lambda`, a primeira implementacao deve calcular e reportar `lambda`, mas nao alterar o algoritmo principal sem justificativa.

## Erro

O enunciado define:

`epsilon = ||r(i+1)||_2 - ||r(i)||_2`

Para criterio de parada, registrar os dois valores:

- `epsilon_signed`;
- `epsilon_abs = abs(epsilon_signed)`.

A parada usara `epsilon_abs < tolerancia` para evitar parada prematura por diferenca negativa grande.

## Controle de saturacao

O servidor deve estimar custo antes de executar:

- linhas e colunas de `H`;
- bytes estimados de `H`, `g`, `f`, `r`, `z`, `p`, `w`;
- tempo medio historico por modelo;
- numero de requisicoes em execucao.

Politica implementada:

- fila de requisicoes limitada (`queue_limit`);
- pool de workers com numero de threads detectado pelo ambiente, nao fixo (`max_workers: "auto"` -> `max(2, nucleos-1)`);
- rejeicao controlada (HTTP 503) quando a fila esta cheia ou quando a estimativa de memoria projetada ultrapassa o limite (`memory_soft_limit_bytes`);
- timeout de espera na fila (HTTP 504).

Observacao honesta: a admissao decide por FILA + MEMORIA estimada, nao por %CPU
(o uso de CPU e medido e reportado, mas nao entra na decisao de aceitar/rejeitar).
O `BoundedSemaphore` tem o mesmo tamanho do pool, entao nao adiciona limite alem
do numero de threads.
