# artigo2md — instruções para o Claude Code

Conversor local de PDF → Markdown com escolha automática de motor (Docling, MarkItDown e,
opcionalmente, MinerU). Código em `src/artigo2md/`:

- `pdfscan.py` — inspeção do PDF com pypdfium2 (páginas escaneadas, tabelas, fórmulas).
- `engines.py` — adaptadores e ficha (`ENGINE_INFO`) dos motores; pymupdf4llm é import opcional,
  o MinerU é chamado como processo externo (`mineru`, só 3.x).
- `router.py` — plano de motores, avaliação de qualidade e fallback.
- `cli.py` — `artigo2md convert | serve | doctor | worker`.
- `web.py` + `static/index.html` — interface web (FastAPI, uma conversão por vez).
- `worker.py` — processo auxiliar de conversão que se encerra após `--idle-timeout` s ocioso
  (devolve a RAM dos modelos). Nunca importe docling/pymupdf4llm no processo principal.
- `jobqueue/` — worker da fila de conversão (`artigo2md worker`, extra `queue`: pika + psutil), que consome
  jobs do RabbitMQ da `stack/` em `127.0.0.1`. Só é importado pelo ramo `worker` de `cli.py` (nunca por
  `convert`, `serve` nem `doctor`); o processo principal do worker não importa docling/pymupdf4llm — a
  conversão roda em processos filhos `spawn`. Módulos: `settings.py` (opções, credenciais, códigos de saída;
  `DEFAULT_WORKERS`/`DEFAULT_MIN_FREE_MEMORY_MB` vêm da medição em
  `docs/specs/conversion-queue/ram-measurement.md`: não os altere sem refazer a medição com
  `tools/measure_worker_ram.py`), `contract.py` (mensagens, `schema_version` 1), `topology.py`, `amqp.py`,
  `slot.py`, `memory.py` (proteção de RAM), `output.py` (destino confinado e gravação atômica) e `service.py`.
  Os testes usam um canal AMQP falso (`tests/fake_amqp.py`); o broker real só com `ARTIGO2MD_STACK=1`.

## Quando o usuário pedir para instalar

1. Confirme que `uv` existe (`uv --version`). Se não, instale:
   `curl -LsSf https://astral.sh/uv/install.sh | sh` (peça confirmação antes).
2. `uv sync --extra dev --extra queue --extra pymupdf` na raiz do projeto. Demora: baixa PyTorch e
   dependências do Docling. (Sem `--extra pymupdf` se o usuário não quiser o motor opcional AGPL; sem
   `--extra queue`, os testes do worker da fila que precisam de pika/psutil são pulados.)
3. `uv run artigo2md doctor` — Docling e MarkItDown têm de aparecer `ok`; pymupdf4llm também,
   se instalado.
4. `uv run pytest` — todos devem passar.
5. Teste real (baixa ~1–2 GB de modelos na primeira vez): `ARTIGO2MD_SLOW=1 uv run pytest -k docling`.
6. Mostre ao usuário como abrir a interface: `uv run artigo2md serve --open`
   (porta padrão 8777; se ocupada, `--port`).

Não instale o MinerU a menos que o usuário peça; é opcional e pesado. Se pedir, use exatamente
`uv tool install "mineru[core]>=3.4,<4" --with six` — a 4.x não serve e sem o `six` a 3.4.5
quebra (README, seção "Motor opcional MinerU").

## Regras do projeto

- Nada pode enviar documentos para serviços externos: a promessa do projeto é ser 100% local.
- Dependências obrigatórias só com licença permissiva (MIT/Apache/BSD). Nada AGPL/GPL no
  `pyproject.toml` — por isso a inspeção usa pypdfium2 e não PyMuPDF.
- Mensagens ao usuário em português.
- Rode `uv run pytest` antes de qualquer commit.
