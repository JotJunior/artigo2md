# Research: Infraestrutura local em containers

**Feature**: `container-stack` | **Date**: 2026-11-24 | **Spec**: [spec.md](spec.md)

> Toda licença, tag de imagem, porta padrão, endpoint de saúde e comando de healthcheck abaixo vem de fonte oficial
> consultada **em 2026-11-24** (data da consulta), na tabela de fontes ao final (S01…S38). Fatos sobre a CLI do
> Docker instalada vêm da saída de `--help` observada nesta máquina na mesma data (S30–S32). Valores marcados
> **[PROPOSTA]** são escolhas de design desta feature (não são fatos externos) e são validados na implementação.

## Decision 1: Imagem do Postgres — `postgres:18-trixie`

**Decision**: usar a imagem oficial `postgres:18-trixie` (Debian trixie), hoje apontando para 18.7 (S02, S03). Volume nomeado montado em `/var/lib/postgresql` (S03: "The defined `VOLUME` was changed in 18 and above to `/var/lib/postgresql`"); o `PGDATA` padrão da 18 é `/var/lib/postgresql/18/docker` (S03). Variáveis: `POSTGRES_PASSWORD` obrigatória ("It must not be empty or undefined"), `POSTGRES_USER` (padrão `postgres`), `POSTGRES_DB` (padrão = `POSTGRES_USER`) (S03). As variáveis só têm efeito com o diretório de dados vazio (S03) — trocar credenciais depois exige apagar o volume (documentado no `.env.example`).

**Rationale**: 18 é suportada até 14/11/2030 (S02); a 19.0 saiu em 19/11/2026 (S02), cinco dias antes desta consulta, e a feature `job-store` vai depender desta base — preferir a major com mais tempo de campo. A tag de major (`18-trixie`) recebe minors de segurança quando o usuário pede atualização (FR-007: download só sob pedido). Base Debian em vez de Alpine: mesma família de SO da imagem da API (Decision 3) e glibc (S03 nota que a Alpine usa musl).

**Alternatives considered**: `19-trixie` (rejeitada: lançada há 5 dias; reavaliar numa atualização futura); `18-alpine` (rejeitada: musl e SO diferente das demais imagens); fixar minor `18.7-trixie` (rejeitada: travaria correções de segurança; a reprodutibilidade vem de só baixar sob pedido).

## Decision 2: Imagem do RabbitMQ — `rabbitmq:4.4-management`

**Decision**: imagem oficial `rabbitmq:4.4-management` (hoje 4.4.1), variante Ubuntu (diretório `4.4/ubuntu/management`) (S12), com Ubuntu 26.04, Erlang/OTP 29.1.2 e OpenSSL 3.5.9 (S14). Volume nomeado em `/var/lib/rabbitmq` (S11: "This image makes all of `/var/lib/rabbitmq` a volume by default"); `hostname` fixo no compose, porque os dados ficam por nome de nó, que deriva do hostname (S11). Credenciais por `RABBITMQ_DEFAULT_USER` / `RABBITMQ_DEFAULT_PASS`, que o README diz estarem "now available in RabbitMQ directly" e não estão na lista de variáveis descontinuadas (S11). Painel de gestão na porta 15672 do container (S07, S11); AMQP na 5672 (S07). Nenhum arquivo `rabbitmq.conf` próprio nesta feature (os padrões bastam).

**Rationale**: a 4.4 tem suporte comunitário até 31/07/2027; a 4.3 só até 31/01/2027 (S06). A variante `-management` já traz o plugin de gestão habilitado, exigido pela FR-001.

**Alternatives considered**: `4.3-management` (rejeitada: suporte comunitário termina em ~2 meses); `4.4-management-alpine` (rejeitada: SO diferente das demais imagens); habilitar o plugin por `enabled_plugins` sobre a imagem sem `-management` (rejeitada: mais configuração sem ganho).

## Decision 3: Imagem base da API — `python:3.13-slim-trixie` + uv `0.13.9`, só o grupo `api`

**Decision**: `FROM python:3.13-slim-trixie` (tag existente hoje, 3.13.18) (S16). O uv é copiado da imagem oficial com versão fixa, como recomenda a doc: `COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /uvx /bin/` (S24; a doc usa 0.13.9 como exemplo de pin, mesma versão do uv instalado nesta máquina, S33). As dependências da API entram por um **dependency group** novo, `api = ["fastapi>=0.115", "uvicorn>=0.30"]` em `[dependency-groups]` (S25), instalado com `uv sync --frozen --only-group api` ("Only include dependencies from the specified dependency group. The project and its dependencies will be omitted.", S26). O código entra copiando `src/artigo2md/` para a imagem e rodando com `PYTHONPATH` apontando para `src`, sem instalar o pacote (que puxaria o Docling). `UV_COMPILE_BYTECODE=1` e `UV_LINK_MODE=copy` como na doc (S24).

**Rationale**: o pacote `artigo2md` declara `docling` como dependência obrigatória (`pyproject.toml:9`); instalar o projeto na imagem traria PyTorch e os modelos e violaria a FR-008 ("a API MUST NOT carregar Docling"). O grupo `api` reaproveita os pins do `uv.lock` (reprodutível) e não muda o que `uv sync` instala por padrão. `src/artigo2md/__init__.py` só define `__version__` (verificado no código), então importar `artigo2md.api` não carrega nada pesado. Python 3.13 respeita `requires-python = ">=3.10"` (`pyproject.toml:7`); o CI segue em 3.12 (`.github/workflows/tests.yml`).

**Alternatives considered**: `pip install --no-deps .` + `pip install fastapi uvicorn` (rejeitada: versões fora do lock); extra `[project.optional-dependencies] api` (rejeitada: um extra sempre soma às dependências obrigatórias, que incluem o Docling); `python:3.12-slim-trixie` (aceitável; 3.13 escolhida por ser a série estável mais recente com tag `slim-trixie` sem pré-release, sem impacto no `requires-python`).

**Impacto no lock**: acrescentar o grupo exige regenerar o `uv.lock` (`uv lock`) na mesma mudança — o CI roda `uv sync --locked` (`.github/workflows/tests.yml`) e falharia com o lock desatualizado.

## Decision 4: Healthchecks

**Decision**:

| Serviço | Teste (forma `CMD`, S27) | Fonte |
|---|---|---|
| postgres | `pg_isready -h 127.0.0.1 -U ${POSTGRES_USER} -d ${POSTGRES_DB}` | S04: exit 0 = "accepting connections normally", 1 = rejeitando (ex.: durante o startup), 2 = sem resposta; "It is not necessary to supply correct user name, password, or database name values" |
| rabbitmq | `rabbitmq-diagnostics -q check_running && rabbitmq-diagnostics -q check_port_connectivity` (forma `CMD-SHELL`) | S08: `check_running` garante que a aplicação não está parada; `check_port_connectivity` "will try to open a TCP connection to the discovered listener ports" |
| api | `python -c` com `http.client` em `http://127.0.0.1:8000/health` (a imagem slim não tem `curl` garantido) | contrato próprio [PROPOSTA], `contracts/api-health.md` |

Tempos [PROPOSTA]: `interval: 5s`, `timeout: 5s`, `retries: 12`, `start_period: 30s`; o script de subir usa `docker compose up --detach --wait --wait-timeout 120` (S29: `--wait` "exits once the services are running or healthy"), alinhado a SC-001 (2 min). As imagens oficiais do Postgres e do RabbitMQ não trazem `HEALTHCHECK` (S03 não menciona; S11: "why this image does not come with a default `HEALTHCHECK`"), por isso o compose define todos.

**Rationale**: o `-h 127.0.0.1` força a checagem por TCP, que é o caminho usado pela API e pelo `doctor` (hipótese de que evita "saudável" pelo socket Unix durante a inicialização: validar no teste de integração). Para o RabbitMQ, `ping` (estágio 1) só prova que o runtime está de pé; o estágio 4 cobre os listeners (S08). A doc avisa que comandos de CLI têm custo por entrar na distribuição Erlang (S08) — intervalo de 5 s é aceitável numa stack local monousuário.

**Alternatives considered**: `rabbitmq-diagnostics -q ping` (rejeitada: não prova listener aberto); checagem TCP pura (rejeitada: o operador definiu "acessível" como handshake de protocolo, block-002); `node_health_check` (descontinuado: "DO NOT USE", S08).

## Decision 5: Publicação de portas só em loopback

**Decision**: no compose, toda porta publicada usa a forma curta com IP literal `"127.0.0.1:${PORTA}:<porta-do-container>"` (S27: `HOST` é `[IP:](port | range)`; exemplo `"127.0.0.1:8001:8001"`). O IP é texto fixo no `compose.yaml`; o `.env` só fornece o número da porta (FR-006). Portas publicadas por padrão: Postgres 5432, AMQP 5672, API 8000 (container); a de gestão 15672 só é publicada com o opt-in `--management-panel`, que acrescenta `stack/compose.management.yaml` (mesma forma, IP literal `127.0.0.1`) — mudança por block-004/dec-023/dec-025, para tratar o risco de DNS rebinding por não publicação. Portas 4369 (epmd) e 25672 (distribuição Erlang) (S07) **não** são publicadas.

**Rationale**: sem IP, o Docker publica em todas as interfaces (`0.0.0.0` e `[::]`) e "Publishing container ports is insecure by default" (S31b, S27). Com `127.0.0.1`, "only the Docker host can access the published container port" (S31b). Dentro do container os serviços escutam nas interfaces do próprio container (o RabbitMQ escuta em todas por padrão, S07), o que é necessário para o encaminhamento de porta e não expõe nada à rede, porque o único caminho de fora é a publicação em loopback.

**Alternatives considered**: mudar o endereço padrão do daemon (S31b) — rejeitada: altera a configuração global do Docker do usuário, fora do blast radius do projeto.

## Decision 6: Sem telemetria — por configuração (mantém dec-010)

**Decision**: nenhum dos serviços tem, na documentação oficial lida, recurso de telemetria, relatório de uso ou verificação de atualização a desligar: nas páginas de networking, management, monitoring e release-information do RabbitMQ não há menção a telemetria, "phone home" ou update check; a única chamada externa descrita é a descoberta OAuth, e só quando `auth_oauth2.issuer` é configurado (S09) — esta feature não configura OAuth. O README da imagem do Postgres e a doc do Postgres consultadas não descrevem envio de dados. A garantia segue a Clarification dec-010: configuração (nada de OAuth, nenhum serviço remoto na stack) + observação (SC-003). O `up` usa `--pull missing` (S30) para baixar imagem só quando ela falta (primeira execução); atualização exige pedido explícito (`--pull always`, opção do script).

**Rationale**: a reavaliação prevista na Clarification (rede sem saída compatível com portas em loopback) não encontrou, nas fontes lidas, confirmação de que uma rede `internal` aceite publicação de portas; sem fonte, mantém-se dec-010.

**Alternatives considered**: rede `internal: true` no compose (não adotada: sem fonte oficial lida que confirme compatibilidade com `ports`).

## Decision 7: Critério "acessível" do `doctor` (block-002, dec-014)

**Decision**: o `doctor` testa cada serviço com handshake de protocolo, sem credenciais, só com a biblioteca padrão (`socket`, `http.client`) e tempo limite curto por serviço [PROPOSTA: 1 s de conexão e 1 s de leitura]:

- **Postgres**: envia `SSLRequest` — Int32 comprimento `8` + Int32 código `80877103` (S05), inteiros em "network byte order (most significant byte first)" (S35) — e considera acessível se a resposta é um byte `S` ou `N` (S04b: "The server then responds with a single byte containing `S` or `N`") ou o byte `E` que abre uma ErrorResponse (S04b: "should also be prepared to handle an ErrorMessage response"; S35: `Byte1('E')` "Identifies the message as an error."); o conteúdo do erro não é exibido (S04b, CVE-2024-10977). Depois fecha o socket.
- **RabbitMQ**: envia o cabeçalho de 8 octetos `'A' 'M' 'Q' 'P' 0 0 9 1` (S15, §4.2.2) e considera acessível se os 3 primeiros octetos da resposta forem um frame de tipo `1` (METHOD) no canal `0` (S15 §4.2.3: header de 7 octetos type/channel/size; "Type = 1, METHOD"; a resposta é o `Connection.Start`, S15 §4.2.2 e S13). Se o servidor responder com o próprio cabeçalho `AMQP…` (versão rejeitada, S15), reporta "inacessível (versão de protocolo incompatível)".
- **API base**: `GET http://127.0.0.1:<porta>/health` com resposta 200 e corpo do contrato `contracts/api-health.md`.

Portas: resolvidas como em Decision 11, item 2 (ambiente → `ARTIGO2MD_STACK_ENV` → `./stack/.env` → padrões do `.env.example`, Decision 9). A API é checada com `http.client` direto, sem proxy (Decision 11, item 6). As três checagens rodam em paralelo (threads), então o pior caso fica perto de 2 s, dentro de SC-005 (5 s).

**Rationale**: atende "não basta conexão TCP aberta; não há checagem autenticada" (block-002) sem nova dependência obrigatória (Princípio II). O fechamento do socket após o handshake pode gerar uma linha de log de conexão encerrada no servidor (S04 avisa sobre log de tentativa com valores incorretos no `pg_isready`); aceitável.

**Alternatives considered**: drivers `psycopg`/`pika` (rejeitada: dependências novas só para diagnóstico); API HTTP de gestão do RabbitMQ (rejeitada: exige credenciais).

## Decision 8: Scripts de subir/derrubar em `sh` POSIX

**Decision**: `stack/up.sh` e `stack/down.sh` em `sh` POSIX, mensagens em português (FR-012). Sempre chamam `docker compose --project-directory stack --env-file stack/.env -f stack/compose.yaml` (flags existentes no Compose v5.6.1 local, S32). Ordem do `up.sh`: (1) `.env` existe? senão sai ≠0 com instrução de `cp stack/.env.example stack/.env` (FR-004); (2) credenciais não vazias e diferentes do marcador de exemplo [PROPOSTA: `troque-esta-senha`]; (3) `docker info` responde (S36)? senão "Docker não disponível" (FR-012); (4) para cada porta de serviço que **ainda não** está rodando no projeto (`docker compose ps --status running --services`, S32), testa se a porta está ocupada no host com `nc -z -w 1 127.0.0.1 <porta>` (ver Decision 11, item 9) e, se ocupada, sai ≠0 informando a porta e o parâmetro (FR-013); (5) `docker compose up --detach --wait --wait-timeout 120 --pull missing` com `--build` ou `--no-build` conforme Decision 11, item 3 (S29, S30); (6) imprime estado de cada serviço e, só com `--management-panel`, a URL do painel `http://127.0.0.1:<porta>/` (sem a opção: "não publicado"). Reexecução sobre stack no ar: se `docker compose ps --status running --services` (S32) já lista os três serviços, o script não chama `up` de novo e informa "já estava no ar" (FR-015); com stack parcial, chama `up`, que termina com os serviços "running or healthy" e os deixa em execução (S29). Que o `up` não recria containers inalterados nem volumes é verificado no teste de integração (sem fonte lida que o afirme). `down.sh` sem opções: `docker compose down` (remove containers e redes; volumes nomeados ficam, S30b); `down.sh --apagar-dados`: pede confirmação digitada e só então `docker compose down --volumes` (S30b: "Remove named volumes declared in the "volumes" section").

**Nota de 2026-11-27 (desktop-stack-control; a decisão acima não foi reescrita)**: com `--management-panel`, `dc()` acrescenta `-f stack/compose.management.yaml` à chamada do Compose (`stack/up.sh:31-36`), então "sempre chamam `-f stack/compose.yaml`" vale só sem a opção; e o atalho "já lista os três serviços" só é tomado quando a publicação atual do painel (`docker compose port rabbitmq 15672`) coincide com o modo pedido; senão o `up` é chamado e recria o RabbitMQ (`stack/up.sh:200-213`; observado em 11.4, `docs/specs/desktop-stack-control/plan.md`, "Resultado de 11.4"). O `down.sh` não recebe o arquivo adicional e mesmo assim remove o container iniciado com ele (mesma observação).

O `.env` é lido pelo Compose via `--env-file` (sem `source` no shell, então nada do arquivo é executado); o próprio script lê só as chaves que valida, linha a linha, sem `eval` (regras em Decision 11, item 1).

**Rationale**: o projeto já é executado em macOS/Linux; `sh` evita nova dependência; o nome de projeto fixo (`name: artigo2md` no topo do compose, S28b) impede duplicação ao rodar de diretórios diferentes.

**Alternatives considered**: scripts em Python (rejeitada: exigiria `uv run` só para orquestrar Docker); Makefile (rejeitada: `make` não é garantido no macOS sem as Command Line Tools).

## Decision 9: Parâmetros e portas padrão [PROPOSTA]

**Decision**: `.env.example` com `ARTIGO2MD_POSTGRES_PORT=5432`, `ARTIGO2MD_RABBITMQ_PORT=5672`, `ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT=15672` (usada só com `up.sh --management-panel`), `ARTIGO2MD_API_PORT=8778`, `POSTGRES_USER=artigo2md`, `POSTGRES_PASSWORD=troque-esta-senha`, `POSTGRES_DB=artigo2md`, `RABBITMQ_DEFAULT_USER=artigo2md`, `RABBITMQ_DEFAULT_PASS=troque-esta-senha`. Portas do host = portas padrão dos serviços (S01b, S07) para facilitar ferramentas externas; a API usa 8778 para não colidir com o `serve` (8777, `src/artigo2md/cli.py:240`, `--port` do `serve`). Variáveis obrigatórias no compose usam `${VAR:?mensagem}` (S28c) para o Compose também recusar valor ausente.

**Rationale**: conflito de porta (ex.: um Postgres de outro projeto em 5432) é tratado pela FR-013 com a instrução do parâmetro a trocar.

## Decision 10: Testes

**Decision**: sem Docker (sempre rodam): `tests/test_infra.py` (checagens do `doctor` contra servidores falsos em `127.0.0.1` porta efêmera, cobrindo acessível/inacessível/parcial/tempo), `tests/test_api_base.py` (`/health` 200, rota desconhecida 404, `/docs`, `/redoc`, `/openapi.json` desligados — S34; subprocesso que importa `artigo2md.api.app` e confere que `docling` e `pymupdf4llm` não estão em `sys.modules`), `tests/test_stack_files.py` (leitura textual de `stack/compose.yaml`: toda entrada de `ports` começa com `127.0.0.1:`; `.env.example` sem credencial real; `stack/.env` no `.gitignore`), `tests/test_stack_scripts.py` (`up.sh` sem `.env` e com credencial de exemplo sai ≠0 sem chamar Docker, via `PATH` com um `docker` falso). Com a stack: `tests/test_stack_integration.py`, só com `ARTIGO2MD_STACK=1` (Princípio IV), cobre US1–US4 (saudável, idempotência, persistência após down/up, portas só em loopback via `docker compose port`, S36).

**Rationale**: SC-007 e FR-014; mesmo padrão do `ARTIGO2MD_SLOW` (`docs/constitution.md`, Princípio IV).

## Decision 11: Endurecimento após os gates `owasp-security` e `validate-documentation` (2026-11-24)

**Decision** (cada item [PROPOSTA], validado por teste na implementação; atributos de compose citados de S27/S37):

1. **Leitura do `.env` pelo `up.sh` igual à do Compose nos casos que importam**: remove prefixo `export `, espaços nas bordas e aspas simples/duplas que envolvem o valor; **recusa** chave duplicada, valor com `$` (seria interpolado pelo Compose, S28c), senha com menos de 12 caracteres, valor de exemplo `troque-esta-senha` e `POSTGRES_HOST_AUTH_METHOD` presente (a imagem aceita esse parâmetro, S03; um método sem senha desligaria a autenticação). Mensagens citam só o **nome** da variável, nunca o valor. `POSTGRES_DB` também ganha `${POSTGRES_DB:?…}` no compose.
2. **`doctor` usa as mesmas portas que a stack**: ordem de resolução por porta — variável de ambiente `ARTIGO2MD_*_PORT` → arquivo indicado em `ARTIGO2MD_STACK_ENV` → `./stack/.env` no diretório corrente → padrão do `.env.example`; mesmo parser seguro do item 1 (em Python, só leitura). A seção do `doctor` diz de onde vieram as portas.
3. **Build da API só quando necessário**: o serviço `api` tem `image: artigo2md-api:local` além de `build`; o `up.sh` roda `docker image inspect artigo2md-api:local` (S36) e usa `--build` só se a imagem não existir ou com `--atualizar-imagens`; nos demais casos usa `--no-build` (S36). Assim reexecutar `up.sh` offline não reconstrói nada (FR-007, FR-015). O Dockerfile copia `pyproject.toml` + `uv.lock` e roda `uv sync --frozen --only-group api` **antes** de copiar `src/` (camadas como em S24).
4. **Contexto de build mínimo**: `.dockerignore` na raiz em modo allowlist — `*`, depois `!pyproject.toml`, `!uv.lock`, `!src/artigo2md/` — para que `stack/.env` (credenciais), `.git` e PDFs nunca entrem no contexto.
5. **Container da API restrito**: `user: "10001:10001"`, `read_only: true`, `tmpfs: [/tmp]`, `cap_drop: [ALL]`, `security_opt: ["no-new-privileges:true"]` (S37: `user` "overrides the user used to run the container process"; `read_only` "read-only filesystem"; `tmpfs`; `cap_drop` "container capabilities to drop"; `security_opt`). Nenhum serviço usa `privileged`, `network_mode` nem monta `/var/run/docker.sock` (verificado no teste estático).
6. **Probe HTTP sem proxy**: o `doctor` fala com a API por `http.client.HTTPConnection("127.0.0.1", porta)` direto, sem `urllib` (que pode usar proxy do sistema); teste com `HTTP_PROXY`/`HTTPS_PROXY` apontando para endereço inválido confirma que a checagem não passa por proxy.
7. **TrustedHost na API base**: `TrustedHostMiddleware` com `allowed_hosts=["127.0.0.1", "localhost"]` (S38: "Enforces that all incoming requests have a correctly set `Host` header"; "a `400` response will be sent"), contra DNS rebinding a partir do navegador, base para a `project-api`. O healthcheck interno chama `http://127.0.0.1:8000/health`, que passa no filtro.
8. **Teste estático mais estrito**: além da forma curta, recusa `ports` longo sem `host_ip: 127.0.0.1` (S27) e as chaves `network_mode`, `privileged` e montagem de `docker.sock`; o teste de integração confere também a saída já interpolada de `docker compose config` (S36).
9. **Porta ocupada portável**: `nc -z -w 1 127.0.0.1 <porta>` (`-w secs` existe no `nc` do macOS, S36); em Linux, conferir no CI; os testes de script usam um `nc` falso no `PATH`.
10. **`down.sh --apagar-dados` sem terminal interativo**: falha fechado (não apaga, exit 1), a não ser que `--sim` seja passado junto, explicitamente.
11. **Endurecimento do Postgres e do RabbitMQ (decisão do operador, block-004 item 2)**: mantêm-se os padrões das imagens oficiais, aplicando APENAS `security_opt: ["no-new-privileges:true"]` (atributo `security_opt`, S37) aos serviços `postgres` e `rabbitmq`. Que as imagens sobem e ficam saudáveis com essa opção NÃO está confirmado em fonte lida: é hipótese verificada no teste de integração (tarefa 6.1.2); se algum dos dois falhar, a tarefa registra bloqueio humano em vez de remover a opção por conta própria.
12. **Versionamento do contrato `/health` (decisão do operador, block-004 item 3)**: definido em `contracts/api-health.md` §Versionamento e compatibilidade (campos estáveis, mudanças só aditivas, consumidores ignoram chaves desconhecidas, `version` não é versão do contrato), com teste de contrato (tarefa 2.2.6).
13. **Conta padrão `guest` do RabbitMQ (checklist security CHK013)**: fatos lidos em 2026-11-24 (S09b e README S11): `guest` é proibido de conectar de hosts remotos e só conecta por loopback, controlado por `loopback_users`, que "includes one entry by default: the user `guest`"; "This user will only be created on first node boot" (sobre `default_user`/`default_pass`); o README da imagem diz que `RABBITMQ_DEFAULT_USER`/`RABBITMQ_DEFAULT_PASS` mudam o par `guest`/`guest`. NENHUMA das duas páginas afirma se o `guest` continua existindo quando as variáveis são definidas — esse ponto é **hipótese não confirmada**. Requisito (não depende da hipótese): a stack NÃO altera `loopback_users` nem habilita `guest` remoto, e nenhum artefato afirma que o `guest` é removido. Validação: o teste de integração (tarefa 6.1) lista os usuários do broker dentro do container e registra aqui o resultado; se o `guest` existir E puder autenticar por rede, bloqueio humano (não corrigir sozinho).
14. **Credenciais fora de testes e logs (CHK014)**: testes (e logs de CI) nunca imprimem valores de credenciais. O teste de integração que inspeciona `docker compose config` (item 8) captura a saída em memória, confere só as linhas `published`/`host_ip` e descarta o resto sem imprimir; mensagens de asserção citam nomes de variáveis, não valores; senhas dos testes são geradas em tempo de execução (não constantes versionadas) e passam ao `up.sh` por um `.env` temporário fora do repositório; a saída de falha do pytest não inclui o conteúdo do `.env` nem o ambiente.

**Riscos aceitos (com justificativa)**:
- Credenciais em variáveis de ambiente ficam visíveis em `docker inspect` para quem já controla o Docker da máquina; o RabbitMQ não oferece `RABBITMQ_DEFAULT_PASS_FILE` desde a 3.9 (S11: "Unavailable in 3.9 and up"), então `secrets` só cobriria metade. Stack local monousuário: documentado no `.env.example`.
- Avaliação da tarefa 4.3.2 (implementação, 2026-11-24): `ghcr.io/astral-sh/uv:0.13.9` fica fixado só pela tag de versão exata; nenhum digest foi observado nem copiado para o repositório, então nada é afirmado sem fonte. Fixar por digest permanece possível numa atualização futura, a partir de um digest observado na hora.
- Tags de imagem não fixadas por digest: o digest exato só pode ser obtido de fonte observada na implementação; a task de implementação avalia fixar `ghcr.io/astral-sh/uv` por digest. Atualizações só ocorrem com `--atualizar-imagens`.
- `/health` expõe a versão do pacote: só em loopback, sem dado sensível.
- Sem trava contra dois `up.sh` simultâneos e sem limite de memória: stack monousuário; a `desktop-stack-control` serializa as chamadas.
- Painel de gestão do RabbitMQ sem proteção contra DNS rebinding (decisão do operador, block-004 item 1): a proteção `TrustedHost` existe só na API base. **Atualização (block-004, dec-023, dec-025, feature `desktop-stack-control`)**: por padrão o painel passou a NÃO ser publicado (o plugin segue habilitado na imagem), então o risco é tratado por não publicação; ele volta a existir só para quem usa o opt-in `sh stack/up.sh --management-panel`, que continua sem proteção contra DNS rebinding e fica a cargo de quem o liga (o app desktop nunca o liga). Responsável: `desktop-stack-control`. Esta feature NÃO afirma que o painel "exige login": essa afirmação só entra em artefato com fonte oficial consultada de fato, e nenhuma foi consultada para este ponto.
- Postgres e RabbitMQ sem os demais endurecimentos (usuário, sistema de arquivos somente leitura, `cap_drop`, limites de recurso) (decisão do operador, block-004 item 2): só `no-new-privileges` é aplicado (item 11); o resto segue os padrões das imagens oficiais. Risco aceito, responsável futuro: `desktop-stack-control`.
- Versão mínima do Docker Engine para a garantia de loopback em Linux: nenhuma fonte lida trata disso; o alvo é macOS (briefing) e o teste de integração verifica a publicação em loopback.

## Observações da integração real (execute-task, fase 6, 2026-11-24)

Tudo abaixo foi OBSERVADO nesta sessão (macOS arm64, Docker Desktop 29.9.2, Compose 5.6.1), com projeto Compose de teste isolado (`COMPOSE_PROJECT_NAME`), portas livres escolhidas na hora, `.env` temporário fora do repositório e senhas geradas em tempo de execução. `tests/test_stack_integration.py` (`ARTIGO2MD_STACK=1`): 7 testes verdes em ~58 s, sem container, volume ou rede de teste remanescente.

1. **`no-new-privileges` em `postgres` e `rabbitmq` (Decision 11 item 11): hipótese CONFIRMADA.** Com a opção aplicada aos três serviços, `postgres:18-trixie` (PostgreSQL 18.7 aarch64) e `rabbitmq:4.4-management` sobem e ficam `healthy`; o teste confere `no-new-privileges:true` em `HostConfig.SecurityOpt` de cada container. Nenhum bloqueio humano foi necessário.
2. **`pg_isready -h 127.0.0.1` durante o init (Decision 4, tarefa 6.2.1): CONFIRMADA pelo log do Postgres.** Em volume novo, o servidor temporário da inicialização (pid 59) registra só `listening on Unix socket ...`; `listening on IPv4 address "0.0.0.0", port 5432` aparece apenas no servidor definitivo, DEPOIS de `PostgreSQL init process complete; ready for start up.`. Logo, a checagem por TCP (`-h 127.0.0.1`) não pode dar "saudável" durante o init. Amostragem em 6 execuções (≈0,2 s entre amostras): `pg_isready` deu 2 (sem resposta) antes e 0 depois; nenhuma amostra foi falso positivo comprovado (1 amostra por execução ficou ambígua pela latência da leitura de logs). A janela de init foi curta neste hardware (< 1 s).
3. **`doctor` com serviços subindo (tarefa 6.2.2): regra de 1.1.3 mantida.** Com `up -d postgres rabbitmq` em volume novo: aos 0,1 s `postgres`/`rabbitmq`/`api` = `inacessível` (conexão recusada); aos 1,3 s `postgres` passou a `acessível`; aos 2,4 s `rabbitmq` também; a `api` ficou `inacessível` até ser iniciada. O Compose ainda marcava `starting` nesses instantes (o primeiro `healthy` veio aos ~5,9 s, por causa do `interval: 5s`): o `doctor` mede handshake, não saúde, como o contrato diz. O caso do Postgres respondendo `ErrorResponse` durante a inicialização NÃO foi observado (o init terminou rápido demais e nenhum reinício com recuperação foi forçado): a regra continua amparada só na documentação já citada na Decision (S35).
4. **`nc -z -w` em Linux (Decision 11 item 9, tarefa 6.2.3): observação PARCIAL.** Num container `ubuntu:24.04` com `netcat-openbsd` 1.226-1ubuntu2 (instalado via apt na hora), `nc -z -w 1 127.0.0.1 <porta>` saiu 0 com porta aberta e 1 com porta fechada, como no macOS. O runner real do CI (`ubuntu-latest`) NÃO foi observado nem se sabe qual `nc` ele traz: a conferência definitiva fica para a primeira execução do CI (o `up.sh` já avisa e pula a checagem de portas quando `nc` não existe).
5. **`up` sobre containers inalterados (plan §Riscos, tarefa 6.1.4): hipótese CONFIRMADA.** `docker compose up --detach --wait --pull missing --no-build` com a stack no ar manteve os mesmos IDs de container, os mesmos `State.StartedAt` e os mesmos volumes; `up.sh` com tudo no ar imprime "já estava no ar" e não chama o Compose.
6. **Nomes reais dos volumes (tarefa 6.2.4):** `<projeto>_pgdata` e `<projeto>_rabbitmqdata`; com o `name: artigo2md` do arquivo, `artigo2md_pgdata` e `artigo2md_rabbitmqdata`. `COMPOSE_PROJECT_NAME` tem precedência sobre o `name:` do arquivo (observado com `docker compose config --format json`), o que permite o isolamento dos testes.
7. **Persistência (SC-004, tarefa 6.1.5):** uma linha no Postgres (gravada por `psql` dentro do container, via socket Unix) e uma fila durável com uma mensagem persistente no RabbitMQ (criadas pela API HTTP do painel, com as credenciais do teste, que não são impressas) continuaram presentes depois de 3 ciclos `down.sh` + `up.sh`; `down.sh` manteve os 2 volumes em todos os ciclos e `down.sh --apagar-dados --sim` os removeu.
8. **Loopback (SC-002):** `docker compose port` devolveu `127.0.0.1:<porta>` para 5432, 5672, 15672 e 8000 (observação feita quando a 15672 ainda era publicada por padrão; hoje a 15672 só é publicada com `--management-panel`); `docker compose config --format json` mostrou `host_ip: 127.0.0.1` em todas as portas publicadas, e nenhuma é a 4369 (epmd) nem a 25672 (distribuição Erlang).
9. **Efeito colateral do isolamento:** o `up.sh` cita `docker compose -p artigo2md logs <serviço>` na mensagem de falha; com `COMPOSE_PROJECT_NAME` diferente o comando sugerido não casaria com o projeto. Só afeta quem usa a variável; no uso normal o projeto é `artigo2md`.
10. **Conta padrão `guest` do RabbitMQ (Decision 11 item 13, tarefa 8.1, observado em 2026-11-24 na fase de convergência):** com `RABBITMQ_DEFAULT_USER`/`RABBITMQ_DEFAULT_PASS` definidos e volume novo (`rabbitmq:4.4-management`, mesmo `compose.yaml`), `rabbitmqctl list_users` dentro do container listou SÓ o usuário configurado (`[administrator]`); `rabbitmqctl authenticate_user guest guest` saiu com código 65 (`user 'guest' - invalid credentials`); `GET /api/whoami` com `guest`/`guest` pela porta do painel publicada em `127.0.0.1` respondeu 401, e o log do broker registrou `HTTP access denied: user 'guest' - invalid credentials`. Nesta versão e com volume novo, o `guest` não existe e não autentica por rede: nenhum bloqueio humano. Vale só para o que foi observado (volume criado já com as variáveis definidas); a stack continua sem alterar `loopback_users`. O teste `test_guest_account_cannot_authenticate_over_network` (`tests/test_stack_integration.py`) cobre a regra (401 pela rede) a cada execução com `ARTIGO2MD_STACK=1`: 8 testes verdes em ~56 s, sem container nem volume de teste remanescente.

## Registro de licenças (insumo da FR-011)

| Item | Licença confirmada | Enquadramento Princípio II | Fonte |
|---|---|---|---|
| PostgreSQL (servidor) | PostgreSQL License — "a liberal Open Source license, similar to the BSD or MIT licenses" | permissiva | S01 |
| RabbitMQ (servidor + plugins tier 1, inclui gestão) | MPL 2.0 ("source code is licensed under the MPL 2.0"); alguns arquivos OCF Apache-2.0 | copyleft fraco, usado sem modificação | S10 |
| Erlang/OTP (runtime dentro da imagem RabbitMQ) | Apache License 2.0 | permissiva | S17 |
| OpenSSL 3.5.9 (dentro da imagem RabbitMQ) | "For the 3.0 release, and later … the Apache License v2 applies" | permissiva | S18 |
| Python 3.13 (runtime da imagem da API) | Python Software Foundation License Version 2 | não copyleft; a fonte o declara GPL-compatível | S19 |
| artigo2md (código da API base) | MIT | permissiva | `pyproject.toml:6` |
| fastapi, uvicorn e dependências transitivas do grupo `api` | já eram dependências obrigatórias do projeto antes desta feature (`pyproject.toml:12-13`); nenhuma dependência nova entra. A lista exata do grupo sai do `uv.lock` na implementação, e cada licença é citada da página oficial do pacote no registro final | **resolvida na tarefa 7.1.2 (2026-11-24)**: lista derivada do `uv.lock` e licenças lidas das páginas oficiais do PyPI, em `stack/THIRD_PARTY_LICENSES.md` (todas permissivas) | PyPI (ver o registro) |
| uv 0.13.9 (só ferramenta de build) | dupla MIT / Apache-2.0 ("licensed under either of") | permissiva | S20 |
| SO base `postgres:18-trixie` e `python:3.13-slim-trixie` | Debian (trixie): "the Debian system and all its components will be free"; seção main só com software livre | **documentado, fora do enquadramento** (dec-013) | S21, S22, S03, S16 |
| SO base `rabbitmq:4.4-management` | Ubuntu 26.04: "an aggregate work of many works, each covered by their own licence(s)" | **documentado, fora do enquadramento** (dec-013) | S23, S14 |

Os READMEs das três imagens oficiais avisam que elas "likely also contain other software which may be under other licenses" e que a conformidade é responsabilidade de quem usa a imagem (S03, S11, S16) — registrado como informação, conforme a interpretação dec-013 (licença do SO base documentada, sem enquadramento). Nenhum item obrigatório é GPL/AGPL.

## Fontes consultadas (todas em 2026-11-24)

| Id | URL | Usado para |
|---|---|---|
| S01 | https://www.postgresql.org/about/licence/ | licença do PostgreSQL |
| S01b | https://www.postgresql.org/docs/current/runtime-config-connection.html | porta padrão 5432 ("5432 by default"); `listen_addresses` padrão `localhost` |
| S02 | https://www.postgresql.org/support/versioning/ | majors suportadas (19.0, 18.7, 17.12, 16.16, 15.20) e datas de fim |
| S03 | https://raw.githubusercontent.com/docker-library/docs/master/postgres/README.md | variáveis, VOLUME/PGDATA na 18+, variantes, seção License |
| S03b | https://raw.githubusercontent.com/docker-library/official-images/master/library/postgres | tags `18.7, 18, 18.7-trixie, 18-trixie` |
| S04 | https://www.postgresql.org/docs/current/app-pg-isready.html | `pg_isready`: opções, timeout padrão 3 s, códigos de saída, sem credenciais |
| S04b | https://www.postgresql.org/docs/current/protocol-flow.html | resposta `S`/`N` ao SSLRequest; ErrorMessage; CVE-2024-10977 |
| S05 | https://www.postgresql.org/docs/current/protocol-message-formats.html | SSLRequest: `Int32(8)`, `Int32(80877103)` |
| S06 | https://www.rabbitmq.com/release-information | séries 4.4.1 (comunitário até 31 Jul 2027), 4.3.8 (até 31 Jan 2027) |
| S07 | https://www.rabbitmq.com/docs/networking | portas 5672, 15672, 4369, 25672; escuta em todas as interfaces por padrão |
| S08 | https://www.rabbitmq.com/docs/monitoring | health checks em estágios; `check_running`, `check_port_connectivity`; custo da CLI |
| S09 | https://www.rabbitmq.com/docs/management | porta 15672, `management.tcp.port`/`ip`; única chamada externa = OAuth configurado |
| S09b | https://www.rabbitmq.com/docs/access-control | usuário `guest` só por loopback (`loopback_users`) |
| S10 | https://raw.githubusercontent.com/rabbitmq/rabbitmq-server/main/LICENSE | licença MPL 2.0 do RabbitMQ |
| S11 | https://raw.githubusercontent.com/docker-library/docs/master/rabbitmq/README.md | variante management, variáveis, `/var/lib/rabbitmq`, hostname, sem HEALTHCHECK, License |
| S12 | https://raw.githubusercontent.com/docker-library/official-images/master/library/rabbitmq | tags `4.4.1-management, 4.4-management, 4-management, management` (4.4/ubuntu/management) |
| S13 | https://raw.githubusercontent.com/rabbitmq/amqp-0.9.1-spec/main/xml/amqp0-9-1.xml | gramática `open-connection = C:protocol-header S:START C:START-OK` |
| S14 | https://raw.githubusercontent.com/docker-library/rabbitmq/master/versions.json | 4.4: ubuntu 26.04, otp 29.1.2, openssl 3.5.9 |
| S15 | https://www.rabbitmq.com/resources/specs/amqp0-9-1.pdf (texto extraído localmente com pypdfium2) | §4.2.2 cabeçalho `'A' 'M' 'Q' 'P' 0 0 9 1`; §4.2.3 frame type/channel/size, Type 1 = METHOD |
| S16 | https://raw.githubusercontent.com/docker-library/docs/master/python/README.md e https://raw.githubusercontent.com/docker-library/official-images/master/library/python | variantes slim (Debian), tag `3.13.18-slim-trixie, 3.13-slim-trixie`, License |
| S17 | https://raw.githubusercontent.com/erlang/otp/master/LICENSE.txt | Erlang/OTP Apache 2.0 |
| S18 | https://openssl-library.org/source/license/ | OpenSSL 3.x Apache 2.0 |
| S19 | https://docs.python.org/3/license.html | PSF License Version 2; GPL-compatível |
| S20 | https://raw.githubusercontent.com/astral-sh/uv/main/README.md (+ LICENSE-MIT, LICENSE-APACHE) | uv dupla MIT/Apache-2.0 |
| S21 | https://www.debian.org/social_contract | Debian 100% livre |
| S22 | https://www.debian.org/legal/licenses/ | main = software livre |
| S23 | https://canonical.com/legal/intellectual-property-policy (redirecionado de ubuntu.com) | Ubuntu "aggregate work … each covered by their own licence(s)" |
| S24 | https://docs.astral.sh/uv/guides/integration/docker/ | `COPY --from=ghcr.io/astral-sh/uv:0.13.9`, `--no-install-project`, `UV_COMPILE_BYTECODE`, `UV_LINK_MODE` |
| S25 | https://docs.astral.sh/uv/concepts/projects/dependencies/ | `[dependency-groups]`, `--only-group` |
| S26 | https://docs.astral.sh/uv/reference/cli/#uv-sync | `--only-group`, `--frozen`, `--locked` |
| S27 | https://raw.githubusercontent.com/compose-spec/compose-spec/main/workload_spec.md | `ports` `[IP:]` (sem IP = `0.0.0.0`), `host_ip`, `healthcheck` (`CMD`/`CMD-SHELL`), `depends_on: service_healthy` |
| S28 | https://raw.githubusercontent.com/compose-spec/compose-spec/main/container_spec.md | `hostname`, `env_file`/`environment` |
| S28b | https://raw.githubusercontent.com/compose-spec/compose-spec/main/04-version-and-name.md | `name` de projeto |
| S28c | https://raw.githubusercontent.com/compose-spec/compose-spec/main/12-interpolation.md | `${VAR:?err}`, `${VAR:-default}` |
| S28d | https://raw.githubusercontent.com/compose-spec/compose-spec/main/05-services.md | valores de `restart` (`no` é o padrão) |
| S29 | https://docs.docker.com/reference/cli/docker/compose/up/ | `--wait`, `--wait-timeout`, `--detach` |
| S30b | https://docs.docker.com/reference/cli/docker/compose/down/ | `-v/--volumes`; padrão remove só containers e redes |
| S31b | https://docs.docker.com/engine/network/port-publishing/ | publicação padrão em todas as interfaces; `127.0.0.1` = só o host |
| S30 | `docker compose up --help` (Docker 29.9.2, Compose v5.6.1, observado nesta máquina) | `--pull ("always"\|"missing"\|"never")`, `--build`, `--wait` |
| S32 | `docker compose --help`, `docker compose ps --help`, `nc -h` (observados nesta máquina) | `--env-file`, `--project-directory`, `-f`, `ps --status running --services`; `nc -z`, `-G conntimo` |
| S33 | `uv --version` (observado nesta máquina) | uv 0.13.9 |
| S34 | https://fastapi.tiangolo.com/tutorial/metadata/ | `docs_url=None`, `redoc_url=None`, `openapi_url=None` desligam a documentação |
| S35 | https://www.postgresql.org/docs/current/protocol-message-types.html e https://www.postgresql.org/docs/current/protocol-message-formats.html | inteiros em network byte order; ErrorResponse começa com `Byte1('E')` |
| S37 | https://raw.githubusercontent.com/compose-spec/compose-spec/main/container_spec.md (2ª leitura, arquivo completo) | `user`, `read_only`, `tmpfs`, `cap_drop`, `security_opt`, `privileged`, `network_mode` |
| S38 | https://fastapi.tiangolo.com/advanced/middleware/ | `TrustedHostMiddleware` (`fastapi.middleware.trustedhost`), `allowed_hosts`, resposta 400 |
| S36 | `docker info --help`, `docker compose logs --help`, `docker compose port --help`, `docker compose config --help`, `docker image inspect --help`, `docker compose up --help` (`--no-build`), `nc -h` (`-w secs`), `docker compose --help` (`-p, --project-name`) e `uvicorn --help` (uvicorn 0.54.0 via `uv run --with uvicorn`), observados nesta máquina | existência dos subcomandos/flags; `uvicorn [OPTIONS] APP`, `--host`, `--port` |
