# APS CGNR - Reconstrucao de imagens

Projeto para a disciplina de Desenvolvimento Integrado de Sistemas.

O objetivo e construir e comparar duas versoes de um servidor de reconstrucao de imagens por CGNR:

- interpretada e nao fortemente tipada: Python + NumPy/OpenBLAS;
- compilada e fortemente tipada: C++ + BLAS/Eigen/OpenBLAS, conforme ambiente disponivel.

O cliente envia a mesma sequencia de sinais para as duas versoes, recebe as imagens reconstruidas e gera metricas comparativas.

Documentos principais:

- `docs/00-handoff-outro-agente.md`: contexto completo para outro agente assumir.
- `docs/manual-de-uso.md`: passo a passo de uso e apresentacao.
- `docs/checklist-requisitos.md`: auditoria requisito por requisito.
- `docs/metricas-e-saturacao.md`: estrategia de metricas, fila, workers, semaforo e carga.

## Fases

1. Validar operacoes basicas com `Dados.zip`.
2. Validar leitura dos modelos `H-1` e `H-2` e sinais `g/G/A`.
3. Implementar CGNR local para o modelo 30x30.
4. Expor o CGNR em servidor Python.
5. Implementar versao C++ equivalente.
6. Implementar cliente, metricas, saturacao e controle adaptativo.

## Dados

Os dados usados nos testes ficam em `data/raw/`.

Os arquivos grandes `H-1.csv.zip` e `H-2.csv.zip` tambem foram mantidos no projeto final para facilitar a execucao em outra maquina. As versoes binarias/cache ficam em `data/`.

## Comandos iniciais

Use o Python com NumPy/OpenBLAS disponivel no ambiente:

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\scripts\audit_data.py
```

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\scripts\test_basic_ops.py
```

Preparar os dados binarios 30x30 para C++:

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\scripts\prepare_binary_data.py --model 30x30
```

Compilar e rodar a versao C++ no WSL:

```powershell
wsl -d Codex-Debian --exec bash -lc "cd /mnt/c/Users/leand/OneDrive/Desktop/APS-CGNR/cpp && make"
```

```powershell
wsl -d Codex-Debian --exec bash -lc "cd /mnt/c/Users/leand/OneDrive/Desktop/APS-CGNR && ./build/cgnr_cpp --h data/H-2.f64 --g data/g-30x30-1.f64 --rows 27904 --cols 900 --width 30 --height 30 --max-iterations 10 --tolerance 0.0001 --image-out outputs/reconstruction-30x30-g-30x30-1-cpp.pgm --json-out outputs/reconstruction-30x30-g-30x30-1-cpp.json"
```

## Servidores

Terminal 1, servidor Python:

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m python.server
```

Terminal 2, gateway HTTP para a versao C++:

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m python.cpp_gateway_server
```

Terminal 3, cliente comparativo:

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\client\compare_client.py --model 30x30 --count 2 --gain scalar
```

Observacao: o gateway HTTP existe para ambientes Windows/WSL onde o `localhost` do WSL nao fica acessivel pelo Windows. A reconstrucao executada por ele continua sendo o binario C++ compilado com OpenBLAS.

Terminal 4, painel de metricas:

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m python.dashboard_server
```

Acesse:

```text
http://127.0.0.1:8010
```

Com o painel aberto, os terminais 1 e 2 sao opcionais: os servidores Python e C++
podem ser ligados/desligados direto pelos botoes nos cartoes, e cada execucao pode
ser acompanhada ao vivo (log em streaming). Detalhes em `docs/manual-de-uso.md` (secao 6.1/6.2).

## Teste de saturacao adaptativo

O teste principal nao usa uma carga fixa. Ele trabalha em janelas: envia requisicoes, mede erro, latencia p95 e tempo medio em fila, e aumenta ou reduz a taxa conforme o servidor aguenta.

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\scripts\load_test.py --server python --model 30x30 --mode adaptive --clients auto
```

Para demonstrar a meta comentada em aula, sem transformar 200 req/min em numero magico:

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\scripts\load_test.py --server python --model 30x30 --mode adaptive --clients auto --target-rate-per-minute 200
```

O resultado fica em:

```text
outputs/load/
```

Gerar relatorio final agregado:

```powershell
& "C:\Users\leand\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\scripts\build_final_report.py
```
