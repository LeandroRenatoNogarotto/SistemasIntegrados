# Analise das referencias

Referencias analisadas:

- `Trabalho dis.zip`, fornecido localmente.
- `https://github.com/TKach1/image_reconstruction_rust`, clonado para leitura.

## Referencia local: Trabalho dis.zip

### Estrutura

Arquivos principais:

- `server_python.py`
- `cliente.py`
- `cliente_teste.py`
- `converter.py`
- `analisador.py`
- `Comandos`

### Ideia geral

O projeto local implementa um servidor Python com FastAPI. Ele tem endpoints separados:

- `/reconstruir/30x30`
- `/reconstruir/60x60`

O cliente sorteia sinais reais e envia para esses endpoints em intervalos aleatorios.

### Pontos fortes

- Usa `FastAPI`, que combina bem com a proposta cliente-servidor.
- Define modelos `30x30` e `60x60` com dimensoes esperadas.
- Faz lazy load da matriz `H`, carregando cada modelo somente quando necessario.
- Usa `scipy.sparse` e salva `H` como `.npz`, o que faz sentido porque as matrizes tem muitos zeros.
- Implementa CGNR de forma proxima ao pseudocodigo.
- Mede tempo, CPU e memoria com `psutil`.
- Registra metricas em `performance_log.csv`.
- Tem controle simples de saturacao por CPU/memoria.
- Usa prioridade maior para o modelo `30x30` e menor para `60x60`.
- Usa `reshape(..., order='F')`, importante porque os dados parecem vir de ambiente estilo Matlab/Fortran.

### Pontos fracos / cuidados

- O servidor atual responde `202 Accepted` e processa em background, mas `cliente_teste.py` parece esperar uma resposta `200` com `f_reconstruido`. Isso indica versoes desalinhadas.
- `BackgroundTasks` do FastAPI nao e uma fila robusta de processamento; e util para prototipo, mas limitado para controle real de saturacao.
- O controle de saturacao e baseado em limites fixos (`80`, `95`, `90`), entao ainda tem "numeros magicos".
- O CGNR para quando `norm(r) < tol`, mas o enunciado fala em `epsilon = ||r(i+1)|| - ||r(i)||`.
- O relatorio de imagem nao grava todos os metadados exigidos dentro da propria imagem.
- E somente Python; nao cobre a comparacao com linguagem compilada.

### O que aproveitar

- FastAPI/REST como protocolo simples.
- Endpoints ou campo `model_id` para diferenciar `30x30` e `60x60`.
- Lazy load/cache de `H`.
- Conversao de `H` para formato otimizado.
- Log CSV de performance.
- Checagem de saturacao antes de aceitar tarefa.
- `reshape(order='F')` para gerar imagem.

## Referencia Rust: image_reconstruction_rust

### Estrutura

Workspace Rust com tres partes:

- `common`: estruturas compartilhadas de request/response/status.
- `server`: servidor Axum/Tokio.
- `client`: cliente assíncrono com envio aleatorio de sinais e monitoramento de status.

### Ideia geral

O cliente envia `ReconstructionRequest` para:

- `POST /reconstruct`

O servidor enfileira o job, processa CGNR e retorna `ReconstructionResult`.

Tambem expoe:

- `GET /status`

para monitorar CPU e memoria.

### Pontos fortes

- Boa separacao entre contrato (`common`), cliente e servidor.
- Usa UUID por requisicao/usuario.
- Usa fila `mpsc` para jobs.
- Usa `Semaphore` para controlar concorrencia por custo estimado de memoria.
- Diferencia custo de `30x30` e `60x60`.
- Usa `spawn_blocking` para isolar trabalho pesado.
- Salva imagem e relatorio CSV.
- Cliente envia sinais aleatorios e tambem monitora `/status`.

### Pontos fracos / cuidados

- Carrega `H` a partir de CSV dentro de cada job. Isso e muito caro; o nosso servidor deve carregar/cachear `H`.
- Usa `ndarray` denso, sem BLAS explicito.
- Nao e C++; para o nosso caso, Rust serve como referencia arquitetural, nao como implementacao final.
- O criterio de parada usa `z_next.dot(z_next) < 1e-4`, diferente do epsilon do enunciado.
- Aplica ganho `gamma` dentro do servidor. Precisamos decidir se o ganho fica no cliente, como o enunciado sugere, ou no servidor.
- O controle de memoria usa constantes fixas (`512`, `1536 MB`). A ideia e boa, mas os valores devem ir para configuracao e/ou ser estimados.

### O que aproveitar

- Estrutura `common`/contrato compartilhado.
- Endpoint unico `/reconstruct` com `model_id`.
- Endpoint `/status`.
- Fila de jobs.
- Semaforo/controle por custo de memoria.
- UUID por requisicao.
- Cliente que envia sinais aleatorios e monitora desempenho ao mesmo tempo.

## Decisoes para o nosso projeto

Vamos usar as referencias como inspiracao, mas com ajustes:

- manter Python + C++ como linguagens finais;
- usar contrato JSON comum entre cliente e servidores;
- preferir endpoint unico `/reconstruct` com `model_id`;
- manter `/status` para CPU/memoria/fila;
- carregar/cachear `H` no servidor, nunca enviar `H` a cada requisicao;
- medir tempo de fila, tempo de reconstrucao, CPU, memoria, iteracoes e erro;
- usar fila e controle adaptativo por custo estimado;
- remover numeros magicos para `config.json`;
- aplicar o CGNR do enunciado com criterio de parada por `epsilon_abs`;
- registrar `lambda` e `c`, mas nao alterar o CGNR com regularizacao sem justificar;
- gerar imagens com metadados exigidos.

## Duvidas ainda abertas

- O ganho `gamma` deve ser aplicado no cliente ou no servidor? O enunciado fala do cliente gerando/enviando sinais, entao a preferencia e aplicar no cliente e registrar o ganho usado.
- Os arquivos `A-30x30-1.csv` e `A-60x60-1.csv` sao sinais com ganho, sinais alternativos ou outro dado experimental? Precisam ser tratados com cautela.
- A versao final deve retornar a imagem diretamente na resposta ou aceitar a tarefa e permitir consulta posterior por `job_id`? Para demonstracao, retornar resultado direto e tambem salvar no servidor e mais simples.
