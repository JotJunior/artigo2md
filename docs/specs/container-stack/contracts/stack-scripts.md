# Contracts: scripts da stack e arquivo de parâmetros

> Nomes de script, opções, mensagens, variáveis e valores padrão são **[PROPOSTA — a validar na implementação]**.
> Flags do Docker Compose citadas vêm da doc oficial e do `--help` local (research S29, S30, S30b, S32, S36).

## Command: `sh stack/up.sh [--atualizar-imagens] [--management-panel]`

> **Atualização (block-004, dec-023, dec-025)**: o painel de gestão do RabbitMQ não é mais publicado por padrão (risco de DNS rebinding). A opção `--management-panel` é o opt-in: acrescenta `stack/compose.management.yaml` (só `services.rabbitmq.ports` com `127.0.0.1:${ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT}:15672`) à chamada do Compose. Os códigos de saída não mudam.

Rodável de qualquer diretório (o script resolve o próprio caminho). **Opções**: `--atualizar-imagens` e `--management-panel` (combináveis); qualquer outra opção ou argumento → exit 2 com a lista de opções válidas em português. `--atualizar-imagens` com algum serviço do projeto já rodando (checado logo depois do passo 4, porque exige o Docker) → exit 2 (`A infraestrutura está no ar. Derrube-a com 'sh stack/down.sh' antes de usar --atualizar-imagens.`, texto literal de `stack/up.sh:174`), sem chamar o Docker para mudar nada: evita recriar containers em uso por um efeito do Compose que nenhuma fonte lida garante. Ordem de validação (para no primeiro erro, sem subir nada):

| Passo | Falha | Exit | Mensagem (pt) contém |
|---|---|---|---|
| 1. `stack/.env` existe | ausente | 2 | `cp stack/.env.example stack/.env` |
| 2. leitura do `.env` (research D11.1) | chave duplicada; valor com `$`; `POSTGRES_HOST_AUTH_METHOD` presente | 2 | nome da variável (nunca o valor) |
| 2b. `POSTGRES_PASSWORD`, `RABBITMQ_DEFAULT_PASS`, `POSTGRES_USER`, `RABBITMQ_DEFAULT_USER`, `POSTGRES_DB` | vazia; senha igual a `troque-esta-senha` ou com menos de 12 caracteres | 2 | nome da variável e "preencha no stack/.env" |
| 3. portas `ARTIGO2MD_*_PORT` (a do painel, `ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT`, só é validada com `--management-panel`) | não numérica ou fora de 1–65535 | 2 | nome da variável |
| 3b. `ARTIGO2MD_DATA_DIR` (pasta de dados do app; `stack/up.sh:140-154`) | vazia ou ausente; não absoluta; não existe; não é pasta; não abre (permissão); não está em forma canônica (igual a `pwd -P`) | 2 | `ARTIGO2MD_DATA_DIR` e como corrigir em `stack/.env` |
| 4. `docker info` e depois `docker compose version` | Docker ausente ou daemon parado (`docker info` falha); ou `docker` presente sem o subcomando `docker compose` (`docker compose version` falha; subcomando existe no Compose v5.6.1 local, `docker compose version --help` observado em 2026-11-24) | 3 | "Docker não está disponível" + como iniciar, ou "Docker Compose não está disponível" + como instalar o plugin; a saída de erro original dos dois comandos de checagem é descartada e substituída pela mensagem em português |
| 5. porta de serviço ainda não rodando neste projeto (a do painel só com `--management-panel` e só se o painel ainda não está publicado) | ocupada no host | 4 | número da porta + variável a trocar |
| 6. `docker compose … up --detach --wait --wait-timeout 120 --pull missing` + `--build` (imagem `artigo2md-api:local` ausente ou `--atualizar-imagens`) ou `--no-build` | serviço não fica saudável | 5 | serviço com problema + `docker compose -p artigo2md logs <serviço>` |

**Saída original do Docker Compose (passo 6)**: é exibida sem alteração (progresso de download, criação e espera), porque é a única informação detalhada de diagnóstico; o script a cerca de mensagens em português: uma linha antes ("Subindo a infraestrutura...") e, depois, o resultado (sucesso ou a explicação da falha com o serviço e o comando de logs). Erros dos passos 1–5 nunca passam pela saída do Compose: são só mensagens do script em português. Primeira execução: o `--pull missing` baixa as imagens que faltam; executar o script de subir conta como o pedido explícito de download (spec, edge case "Máquina sem internet"). Sem rede e com imagem ausente, o passo 6 falha e cai na linha de exit 5 (o texto exato do Compose nesse caso não é afirmado aqui).

Sucesso (exit 0): uma linha por serviço com estado `saudável` e, **com `--management-panel`**, `Painel do RabbitMQ: http://127.0.0.1:<ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT>/` seguida de um aviso de que o painel fica sem proteção contra DNS rebinding; **sem a opção**, `Painel do RabbitMQ: não publicado (use --management-panel)` e nenhuma URL. Se os três serviços já estavam rodando: "A infraestrutura já estava no ar." seguida da linha de estado do painel (`Painel do RabbitMQ: <URL>` com o aviso, ou `Painel do RabbitMQ: não publicado (use --management-panel)`; `stack/up.sh:209-211`, `print_panel_status`) e exit 0, sem chamar `up` — mas só quando a publicação atual do painel (consultada com `docker compose port rabbitmq 15672`) coincide com o modo pedido; sem a opção e com o painel publicado, o script segue e recria o RabbitMQ sem ele (e com a opção e o painel não publicado, recria-o com ele).

`--atualizar-imagens`: troca `--pull missing` por `--pull always` e força `--build` (único caminho que baixa imagem nova ou reconstrói a API — FR-007 / Princípio I), sempre com a stack derrubada (ver opções acima).

**Medição de SC-001**: pressupõe imagens oficiais baixadas e `artigo2md-api:local` já construída (spec SC-001); o `--build` da primeira execução fica fora da medição.

## Command: `sh stack/down.sh [--apagar-dados [--sim]]`

> Uso conforme `stack/down.sh:4,39` (`--sim` só é aceito junto com `--apagar-dados`).

| Caso | Ação | Exit |
|---|---|---|
| sem opção | `docker compose … down` — containers e rede removidos, volumes `pgdata` e `rabbitmqdata` mantidos. O script não passa `stack/compose.management.yaml`, mas o `down` remove também o container do RabbitMQ iniciado com ele (observado em 11.4, `docs/specs/desktop-stack-control/plan.md`, "Resultado de 11.4"; asserção em `tests/test_stack_integration.py`, `test_panel_opt_out_recreates_rabbitmq_and_down_removes_it`): é um segundo jeito de fechar o painel aberto pelo terminal | 0 |
| `--apagar-dados` | pede que o usuário digite `apagar`; com outra resposta, ou sem terminal interativo, não remove nada e sai 1; confirmado (ou com `--sim` explícito junto), `docker compose … down --volumes` | 0 / 1 |
| Docker indisponível (inclui `docker compose` ausente, como no `up.sh` passo 4) | mensagem em português | 3 |
| opção desconhecida, ou `--sim` sem `--apagar-dados` | nada é executado; mensagem em português com as opções válidas (`--sim` só vale junto com `--apagar-dados`) | 2 |

`down.sh` não exige `stack/.env` completo (para conseguir derrubar mesmo com o arquivo quebrado); se ele faltar, usa `stack/.env.example` só para a interpolação.

## Arquivo `stack/.env.example` (versionado) → `stack/.env` (ignorado pelo git)

| Variável | Padrão no exemplo | Uso |
|---|---|---|
| `ARTIGO2MD_POSTGRES_PORT` | `5432` | porta do host para o Postgres (container 5432) |
| `ARTIGO2MD_RABBITMQ_PORT` | `5672` | porta do host para AMQP (container 5672) |
| `ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT` | `15672` | porta do host para o painel de gestão (container 15672); usada só com `up.sh --management-panel` (sem a opção, o painel não é publicado e a variável é ignorada) |
| `ARTIGO2MD_API_PORT` | `8778` | porta do host para a API base (container 8000) |
| `POSTGRES_USER` | `artigo2md` | superusuário criado na 1ª inicialização |
| `POSTGRES_PASSWORD` | `troque-esta-senha` | obrigatória; o `up.sh` recusa o valor de exemplo |
| `POSTGRES_DB` | `artigo2md` | banco criado na 1ª inicialização |
| `RABBITMQ_DEFAULT_USER` | `artigo2md` | usuário criado na 1ª inicialização |
| `RABBITMQ_DEFAULT_PASS` | `troque-esta-senha` | obrigatória; o `up.sh` recusa o valor de exemplo |

Comentários do arquivo em português; avisam que as senhas precisam de 12+ caracteres sem `$`, que as credenciais ficam visíveis em `docker inspect` para quem controla o Docker da máquina (risco aceito, research D11), que credenciais só valem na 1ª inicialização do volume (fonte: README da imagem postgres, research S03) e que trocar depois exige `down.sh --apagar-dados`. Nenhuma variável controla o endereço de publicação (FR-006).

## Arquivo `stack/compose.yaml`

- `name: artigo2md` (nome de projeto fixo).
- Serviços `postgres`, `rabbitmq`, `api`; todo `ports` na forma `"127.0.0.1:${ARTIGO2MD_…_PORT:?…}:<porta-container>"`. O `compose.yaml` publica só `5432`, `5672` e `8000` (a 15672 não consta); a publicação do painel está em `stack/compose.management.yaml`, na mesma forma com IP literal `127.0.0.1`.
- Volumes nomeados `pgdata` (→ `/var/lib/postgresql`) e `rabbitmqdata` (→ `/var/lib/rabbitmq`).
- `rabbitmq` com `hostname: rabbitmq`.
- `postgres` e `rabbitmq`: `security_opt: ["no-new-privileges:true"]` e nenhum outro endurecimento além dos padrões das imagens oficiais (research D11.11, decisão do operador em block-004); a compatibilidade da opção com as imagens é validada no teste de integração.
- Proibido em qualquer serviço: `privileged`, `network_mode`, montagem de `/var/run/docker.sock`, `ports` sem `127.0.0.1`.
- `api` com `image: artigo2md-api:local` + `build` (contexto = raiz do repo, Dockerfile `stack/api/Dockerfile`), `user: "10001:10001"`, `read_only: true`, `tmpfs: [/tmp]`, `cap_drop: [ALL]`, `security_opt: ["no-new-privileges:true"]` (research D11.5) e `depends_on` de `postgres` e `rabbitmq` com `condition: service_healthy` [PROPOSTA: mantém a ordem que a `project-api` vai precisar].
- Sem `restart` (padrão `no`, research S28d): a stack só sobe quando o usuário (ou o app desktop, futuramente) pede.
