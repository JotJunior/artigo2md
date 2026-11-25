# artigo2md

Converte PDFs — artigos científicos, livros, diretrizes, capítulos — em **Markdown**, no seu
próprio computador. Nada é enviado para a internet e não há custo por página.

Cada PDF é **inspecionado** antes (páginas escaneadas, tabelas, fórmulas), vai para o
**motor mais adequado**, e o resultado passa por uma **avaliação de qualidade**. Se ficou
ruim (por exemplo, o artigo cita "Tabela 2" e nenhuma tabela saiu), o próximo motor é tentado
automaticamente e o melhor resultado é mantido.

## Motores

| Motor | Licença | Papel | Peso | Instalação |
|---|---|---|---|---|
| [Docling](https://github.com/docling-project/docling) (IBM) | MIT | Principal: 2 colunas, tabelas estruturadas, OCR em páginas escaneadas (no Mac, OCR nativo da Apple) | pesado (~1–2 GB de modelos) | incluído |
| [MarkItDown](https://github.com/microsoft/markitdown) (Microsoft) | MIT | Último recurso para PDFs de texto corrido | leve | incluído |
| [pymupdf4llm](https://github.com/pymupdf/pymupdf4llm) | AGPL-3.0 | Reserva rápida: menos de 1 s por artigo, bom em 2 colunas | leve (~30 MB) | opcional: `uv sync --extra pymupdf` |
| [MinerU](https://github.com/opendatalab/MinerU) 3.x | AGPL-3.0 | Fórmulas e layouts complexos | pesado (vários GB) | opcional: ver abaixo |

Os motores AGPL são **opcionais** — nunca dependência obrigatória — por isso o projeto
continua MIT. A interface mostra essa tabela com o que está instalado em cada máquina.

Medição num artigo da JAMA Psychiatry (10 páginas, 2 colunas) num Mac mini M-series:

| Motor | Tempo | Tabelas estruturadas |
|---|---|---|
| Docling | 13 s | 10 |
| pymupdf4llm | 7 s | 4 |
| MinerU | 208 s (inclui carregar modelos) | 7 (em HTML) |
| MarkItDown | 1,5 s | não estrutura; transforma o layout em pseudo-tabelas |

## Instalação

Requer [uv](https://docs.astral.sh/uv/) e ~3 GB livres (modelos do Docling, baixados na
primeira conversão; depois disso o Docling não consulta mais o Hugging Face). Testado em macOS com Apple Silicon e Linux.

```bash
git clone https://github.com/drvalber/artigo2md.git
cd artigo2md
uv sync --extra pymupdf          # ou só `uv sync`, sem o motor opcional pymupdf4llm
uv run artigo2md doctor
```

> Usa o Claude Code? Abra a pasta do projeto e peça: **"instale e teste este projeto"**.
> O `CLAUDE.md` tem o roteiro completo.

## Uso

**Interface web** (recomendado):

```bash
uv run artigo2md serve --open
```

Abre `http://localhost:8777`. Arraste PDFs ou uma pasta inteira; acompanhe a fila, veja o
Markdown, baixe um `.md` ou todos num `.zip`. Os arquivos ficam em `~/artigo2md-dados`.

Para acessar de outra máquina da sua rede (ex.: um servidor em casa via Tailscale):
`uv run artigo2md serve --host 0.0.0.0`. Não há senha — não exponha à internet aberta.

**Memória.** Os modelos ocupam de 1 a 2 GB de RAM depois de carregados (o MinerU, mais ~2 GB
enquanto converte). Por isso as conversões rodam num processo auxiliar que **se encerra
sozinho após 5 minutos sem uso**: em lote ele fica carregado e rápido; parado, a interface
volta a ocupar ~40 MB. Ajuste com `--idle-timeout SEGUNDOS` (`0` = nunca descarregar, bom
para quem converte o dia todo numa máquina com RAM de sobra). O rodapé da interface mostra
se o conversor está carregado.

**Placa de vídeo (NVIDIA).** Docling e MinerU usam a GPU sozinhos se o PyTorch a reconhecer.
Se outro programa estiver ocupando a memória da placa e faltar espaço, a conversão é refeita
no processador automaticamente (mais lenta, mas não falha); o relatório mostra o motor como
`docling+cpu` ou `mineru+cpu`. Antes de chamar o MinerU, o artigo2md descarrega os modelos do
Docling para liberar a placa.

**Linha de comando:**

```bash
uv run artigo2md convert artigo.pdf                     # → ./markdown/artigo.md
uv run artigo2md convert ~/Artigos -o ~/Artigos-md      # pasta inteira, recursivo; a saída repete as subpastas
uv run artigo2md convert livro.pdf -e docling --report  # força o motor e grava relatório
```

Motores: `auto` (padrão), `docling`, `pymupdf`, `mineru`, `markitdown`. Arquivos já
convertidos são pulados (use `--overwrite` para refazer).

## Como o motor é escolhido (`-e auto`)

1. PDF com muitas fórmulas **e** MinerU instalado → MinerU primeiro.
2. Senão → Docling (com OCR ligado se houver páginas escaneadas).
3. Se a nota ficar abaixo de 65/100 → pymupdf4llm → MinerU → MarkItDown, pulando os que não
   estão instalados. pymupdf4llm e MarkItDown não fazem OCR, então são pulados em PDFs
   totalmente escaneados.
4. Fica o resultado de maior nota.

A nota cai quando: o PDF cita tabelas e nenhuma foi estruturada (−45); o Markdown tem menos
de 100 caracteres por página (−35); a saída tem menos da metade do texto nativo do PDF (−20).
Tabelas contam tanto em Markdown quanto em HTML.

## Motor opcional MinerU

Não vem incluído (licença AGPL e vários GB de modelos). Instale à parte, de forma que o
comando `mineru` fique no PATH — o artigo2md detecta sozinho:

```bash
uv tool install "mineru[core]>=3.4,<4" --with six
uv run artigo2md doctor       # deve mostrar MinerU: ok
```

Os modelos (~3 GB) são baixados na primeira conversão. Se o `mineru` estiver em outro lugar,
aponte com `ARTIGO2MD_MINERU=/caminho/para/mineru`.

Por que essas restrições:

- **Só a linha 3.x.** O MinerU 4 virou um serviço em segundo plano com processamento local
  desligado por padrão e telemetria ligada; o artigo2md recusa a 4.x e explica no `doctor`.
- **`--with six`.** O MinerU 3.4.5 usa o pacote `six` sem declará-lo; sem ele, toda conversão
  falha com uma mensagem enganosa sobre o `torch`.
- O artigo2md roda o MinerU sempre com `MINERU_TELEMETRY_DISABLED=1`.

## Infraestrutura local (versão desktop)

Opcional: a conversão (`convert`) e a interface web continuam funcionando sem nada disto. Para a versão
desktop, `stack/` traz uma infraestrutura local em containers (Docker): **Postgres**, **RabbitMQ** (com
painel de gestão) e uma **API base** com `GET /health`. Tudo roda só na sua máquina: as portas são
publicadas apenas em `127.0.0.1` e nenhum serviço envia dados para fora.

```bash
cp stack/.env.example stack/.env     # depois edite o stack/.env e troque as duas senhas
sh stack/up.sh                       # sobe tudo e espera ficar saudável (até 2 min)
uv run artigo2md doctor              # a seção "Infraestrutura" mostra os três serviços
sh stack/down.sh                     # derruba; os dados (volumes) são mantidos
sh stack/down.sh --apagar-dados      # derruba e APAGA os dados (pede que você digite "apagar")
```

- **Senhas**: 12 ou mais caracteres, sem `$`, diferentes do valor de exemplo (`troque-esta-senha`). O
  `up.sh` confere o `stack/.env` antes de chamar o Docker e cita só o nome da variável, nunca o valor.
  As credenciais só valem na primeira inicialização dos volumes; para trocá-las depois, use
  `--apagar-dados`. Em automação, `sh stack/down.sh --apagar-dados --sim` apaga sem perguntar (sem
  terminal interativo e sem `--sim`, nada é apagado).
- **Portas** (mude no `stack/.env` se alguma estiver ocupada; o `up.sh` avisa e sai com código 4):
  Postgres `5432`, RabbitMQ `5672`, painel do RabbitMQ `15672`, API `8778`. O `up.sh` mostra no final a
  URL do painel. O `doctor` lê as mesmas portas (variável de ambiente, depois o arquivo indicado em
  `ARTIGO2MD_STACK_ENV`, depois `./stack/.env`, depois o padrão).
- **Imagens**: a primeira execução baixa as imagens oficiais e constrói `artigo2md-api:local`; depois
  nada é baixado nem reconstruído sem pedido explícito: `sh stack/up.sh --atualizar-imagens` (com a
  stack derrubada) é o único caminho para baixar imagens novas.
- **Códigos de saída do `up.sh`**: 0 ok, 2 parâmetros inválidos, 3 Docker indisponível, 4 porta
  ocupada, 5 serviço não ficou saudável.
- **Licenças** dos componentes (serviços, imagens e dependências da API): `stack/THIRD_PARTY_LICENSES.md`.

Riscos aceitos desta primeira versão (decisões registradas em `docs/specs/container-stack/research.md`):

- as credenciais ficam visíveis em `docker inspect` para quem já controla o Docker desta máquina
  (stack local de um só usuário);
- as imagens são escolhidas por tag, sem fixar digest (as tags podem mudar entre atualizações);
- o painel do RabbitMQ não tem proteção contra DNS rebinding (a API base tem; o responsável futuro é
  `desktop-stack-control`);
- Postgres e RabbitMQ seguem os padrões das imagens oficiais, com `no-new-privileges` como único
  endurecimento adicional (a API roda sem privilégios, com sistema de arquivos somente leitura);
- o `doctor` fecha a conexão logo após o handshake de protocolo, o que pode deixar uma linha de
  "conexão encerrada" nos registros do Postgres e do RabbitMQ.

Testes: `uv run pytest` roda sem Docker; com Docker disponível,
`ARTIGO2MD_STACK=1 uv run pytest tests/test_stack_integration.py` sobe uma cópia isolada da stack (projeto
Compose, portas e senhas próprios) e a derruba ao final.

## Desenvolvimento

```bash
uv sync --extra dev --extra pymupdf
uv run pytest                       # rápido
ARTIGO2MD_SLOW=1 uv run pytest      # inclui o teste real com Docling (baixa modelos)
ARTIGO2MD_STACK=1 uv run pytest tests/test_stack_integration.py   # integração com Docker
```

## Licença

MIT. Os motores mantêm suas próprias licenças; pymupdf4llm e MinerU (AGPL) são opcionais e
não são distribuídos com este projeto.
