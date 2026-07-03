# Resultados parciais

## Auditoria dos dados

Modelo 30x30:

- `H-2.csv.zip` contem `H-2.csv`;
- dimensao confirmada: `27904 x 900`;
- sinais `g-30x30-1.csv` e `g-30x30-2.csv` tem `27904` valores.

Modelo 60x60:

- `H-1.csv.zip` contem `H-1.csv`;
- dimensao confirmada: `50816 x 3600`;
- sinais `G-1.csv` e `G-2.csv` tem `50816` valores.

Observacao:

- `A-30x30-1.csv` e `A-60x60-1.csv` tambem tem tamanhos compativeis com os sinais, mas amplitudes muito maiores. O papel exato deles deve ser confirmado antes de usar como sinal padrao.

## Atividade 2 - Operacoes basicas

Validado em Python/NumPy e C++/OpenBLAS:

- `MN = M * N`;
- `aM = a * M`;
- `Ma = M * a^T`;
- `a0M = a0 * M` como teste de multiplicacao escalar.

Resultados:

- `MN` bateu exatamente;
- `aM` bateu com diferenca maxima `0.00467`, esperada por arredondamento do arquivo;
- `Ma` executou com dimensao correta.

## CGNR 30x30

Entrada:

- modelo: `30x30`;
- matriz: `27904 x 900`;
- sinal: `g-30x30-1.csv`;
- tolerancia: `1e-4`;
- iteracoes maximas: `10`.

Python/NumPy:

- iteracoes: `2`;
- erro absoluto: `6.165833491e-05`;
- residual: `7.062889455e-05`;
- lambda: `3.902965116e-09`;
- fator de reducao estimado: `8.150867254e-08`;
- tempo real observado: aproximadamente `240 ms`.

C++/OpenBLAS:

- iteracoes: `2`;
- erro absoluto: `6.16583e-05`;
- residual: `7.06289e-05`;
- lambda: `3.9e-09`;
- fator de reducao estimado: `8.15e-08`;
- tempo real observado: aproximadamente `449 ms`.

Observacao:

- o tempo de CPU pode ser maior que o tempo real porque OpenBLAS usa multiplas threads internamente.

## CGNR 60x60

Entrada:

- modelo: `60x60`;
- matriz: `50816 x 3600`;
- sinal testado: `G-2.csv`;
- tolerancia: `1e-4`;
- iteracoes maximas: `10`.

Python/NumPy:

- status: `ok`;
- iteracoes: `3`;
- erro absoluto: aproximadamente `8.21446549e-05`;
- tempo de reconstrucao observado: aproximadamente `2319 ms`.

C++/OpenBLAS:

- status: `ok`;
- iteracoes: `3`;
- erro absoluto: aproximadamente `8.21447e-05`;
- tempo de reconstrucao observado: aproximadamente `2195 ms`.

Observacao:

- o roundtrip da versao C++ ficou maior porque o gateway HTTP chama o binario C++ no WSL por requisicao. O nucleo de reconstrucao ainda e compilado/OpenBLAS, mas existe overhead de processo e leitura da matriz.
