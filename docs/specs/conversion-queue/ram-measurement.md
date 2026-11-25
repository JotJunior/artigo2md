# Medição de RAM do worker da fila

Gerado por `tools/measure_worker_ram.py report` a partir da saída bruta da execução; cada número abaixo
vem dessa saída, sem valor digitado à mão ou estimado. Unidades: MB = 1 MiB (1048576 bytes).

## Máquina

- Modelo (`sysctl -n hw.model`): `Mac17,14`
- Memória total (`sysctl -n hw.memsize`): 68719476736 bytes (64 GiB); `psutil.virtual_memory().total` = 65536 MB
- Sistema (`sw_vers`): ProductName:		macOS | ProductVersion:		27.2.1 | BuildVersion:		26B131
- Python 3.12.16, psutil 7.2.2, macOS-27.2.1-arm64-arm-64bit

## PDFs do lote

| Nome | Páginas | Perfil | Bytes | SHA-256 |
|------|--------:|--------|------:|---------|
| `texto-nativo.pdf` | 12 | texto nativo | 10584 | `82d539d220ed5c855e2e48fcb7db23ec013abfdca6f4a9069b63f9c300a8f04e` |
| `escaneado.pdf` | 6 | escaneado (só imagem) | 462168 | `4a806ce9cdd0d27ec1860c05f1315a637ef6c857d32b069753d682addf14fcb7` |
| `tabelas.pdf` | 6 | com tabelas | 10725 | `e3186c164d3554a4af78982ea05cc98571350044d42a7135eedf94fb052898bb` |

Lote repetido 2 vez(es) por tamanho de pool, motor `auto`. PDFs sintéticos gerados por `tools/measure_worker_ram.py make-batch`; nenhum PDF de usuário.

## Tamanhos de pool testados

| Pool | `--min-free-memory` (MB) | Conversores vistos | Pico por filho (MB) | Pico do conjunto de filhos (MB) | Menor `available` (MB) | Adiamentos do gate | Eventos `result`/`error` | Saída do worker |
|-----:|------------------------:|--------------:|--------------------:|-------------------------------:|-----------------------:|-------------------:|-------------------------:|----------------:|
| 1 | 0 | 1 | 3989 | 3989 | 25059 | 0 | 7/0 | 0 |
| 2 | 0 | 2 | 4004 | 7385 | 23824 | 0 | 8/0 | 0 |
| 3 | 0 | 3 | 3841 | 10886 | 22311 | 0 | 9/0 | 0 |
| 4 | 0 | 4 | 3855 | 14055 | 20550 | 0 | 10/0 | 0 |
| 4 | 19817 | 2 | 4018 | 7462 | 22701 | 6 | 10/0 | 0 |

## Consumo por processo de conversão

### Pool 1

Métrica de memória: `footprint_mb` (USS indisponível sem privilégio de administrador no macOS: `psutil` devolve `AccessDenied`; usada a pegada física do `footprint`).

| PID | Amostras | Ocioso (1ª amostra) | Carregado (após o aquecimento) | Pico | RSS ocioso | RSS carregado | RSS pico |
|----:|---------:|--------------------:|-------------------------------:|-----:|-----------:|--------------:|---------:|
| 70415 | 46 | 18 | 3416 | 3989 | 28 | 1221 | 1749 |

### Pool 2

Métrica de memória: `footprint_mb` (USS indisponível sem privilégio de administrador no macOS: `psutil` devolve `AccessDenied`; usada a pegada física do `footprint`).

| PID | Amostras | Ocioso (1ª amostra) | Carregado (após o aquecimento) | Pico | RSS ocioso | RSS carregado | RSS pico |
|----:|---------:|--------------------:|-------------------------------:|-----:|-----------:|--------------:|---------:|
| 72657 | 42 | 20 | 3250 | 3536 | 28 | 1225 | 1354 |
| 72658 | 42 | 23 | 3432 | 4004 | 34 | 1295 | 1806 |

### Pool 3

Métrica de memória: `footprint_mb` (USS indisponível sem privilégio de administrador no macOS: `psutil` devolve `AccessDenied`; usada a pegada física do `footprint`).

| PID | Amostras | Ocioso (1ª amostra) | Carregado (após o aquecimento) | Pico | RSS ocioso | RSS carregado | RSS pico |
|----:|---------:|--------------------:|-------------------------------:|-----:|-----------:|--------------:|---------:|
| 73464 | 44 | 19 | 3434 | 3807 | 29 | 1297 | 1604 |
| 73465 | 44 | 22 | 3252 | 3841 | 34 | 1228 | 1595 |
| 73466 | 44 | 28 | 3407 | 3408 | 40 | 1221 | 1252 |

### Pool 4

Métrica de memória: `footprint_mb` (USS indisponível sem privilégio de administrador no macOS: `psutil` devolve `AccessDenied`; usada a pegada física do `footprint`).

| PID | Amostras | Ocioso (1ª amostra) | Carregado (após o aquecimento) | Pico | RSS ocioso | RSS carregado | RSS pico |
|----:|---------:|--------------------:|-------------------------------:|-----:|-----------:|--------------:|---------:|
| 73913 | 45 | 20 | 3286 | 3595 | 29 | 1262 | 1412 |
| 73914 | 45 | 24 | 3425 | 3476 | 36 | 1278 | 1299 |
| 73915 | 45 | 30 | 3413 | 3413 | 43 | 1227 | 1241 |
| 73916 | 45 | 33 | 3444 | 3855 | 48 | 1274 | 1635 |

### Pool 4 (execução do gate)

Métrica de memória: `footprint_mb` (USS indisponível sem privilégio de administrador no macOS: `psutil` devolve `AccessDenied`; usada a pegada física do `footprint`).

| PID | Amostras | Ocioso (1ª amostra) | Carregado (após o aquecimento) | Pico | RSS ocioso | RSS carregado | RSS pico |
|----:|---------:|--------------------:|-------------------------------:|-----:|-----------:|--------------:|---------:|
| 89007 | 53 | 17 | 3878 | 4018 | 29 | 1617 | 1709 |
| 89144 | 34 | 51 | 3434 | 3540 | 78 | 1237 | 1363 |

Conversor = processo `multiprocessing.spawn` iniciado pelo `ConversionWorker`; os demais descendentes do worker (por exemplo, o `multiprocessing.resource_tracker`) ficam na saída bruta, com o papel `helper`, e não entram nas colunas acima.

## Comportamento da proteção de RAM (gate)

- Pool 4 com `--min-free-memory 19817`: 6 aviso(s) "Memória livre insuficiente" no log do worker; 2 processo(s) de conversão iniciado(s); eventos finais `result`/`error`: 10/0.

## Memória disponível ao longo do lote

| Pool | Início do run (MB) | Mínimo (MB) | Fim do run (MB) |
|-----:|-------------------:|------------:|----------------:|
| 1 | 27922 | 25059 | 26450 |
| 2 | 28449 | 23824 | 26330 |
| 3 | 30074 | 22311 | 25814 |
| 4 | 31438 | 20550 | 25429 |
| 4 (gate) | 25783 | 22701 | 25175 |


## Derivação dos padrões de `--workers` e `--min-free-memory`

Regra (`research.md`, Decision 8, com as constantes extraídas só dos números acima; `tools/measure_worker_ram.py derive` reproduz esta seção a partir da saída bruta):

- Métrica de memória: `footprint_mb`. Pico por conversor = maior pico observado em todos os runs = **4018 MB**; menor pico de um conversor = 3408 MB.
- `margem` = dispersão observada entre conversores (maior pico ÷ menor pico = 1.179), arredondada para cima em passos de 0.05 = **1.20**.
- `reserva_do_sistema` = memória ocupada pelo resto do sistema no pior início observado = total (65536 MB) − menor `available` na primeira amostra de um run (25783 MB) = **39754 MB**.
- `min_free_memory` = ⌈4018 × 1.20⌉ = **4822 MB**.
- `workers` pela fórmula = ⌊(65536 − 39754) ÷ 4018⌋ = 6; tamanhos de pool efetivamente medidos: [1, 2, 3, 4]; o padrão não extrapola além do maior pool medido (4): **`workers` padrão = 4**.

Limites da medição (nada além disto foi medido): lote sintético pequeno (ver tabela de PDFs), uma máquina, motor `auto`; PDFs maiores ou de outros perfis podem exigir mais memória, e a proteção de RAM (FR-005) continua valendo em máquinas menores que a medida. USS indisponível sem privilégio de administrador no macOS; a pegada física (`footprint`) é a métrica usada.

```json
{
  "metric": "footprint_mb",
  "peak_mb": 4018.018394470215,
  "margin": 1.2,
  "total_mb": 65536.0,
  "system_reserve_mb": 39754,
  "min_free_memory_mb": 4822,
  "workers_by_formula": 6,
  "largest_measured_pool": 4,
  "default_workers": 4,
  "pool_sizes_measured": [
    1,
    2,
    3,
    4
  ]
}
```
