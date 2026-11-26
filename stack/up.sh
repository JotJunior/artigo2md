#!/bin/sh
# Sobe a infraestrutura local do artigo2md (Postgres, RabbitMQ com painel e API do app).
#
# Uso:  sh stack/up.sh [--atualizar-imagens]
#
# Roda de qualquer diretório. Valida o stack/.env ANTES de chamar o Docker e nunca imprime o valor
# de nenhuma credencial: as mensagens citam só o nome da variável.
#
# Códigos de saída: 0 ok | 2 parâmetros inválidos | 3 Docker indisponível | 4 porta ocupada |
#                   5 serviço não ficou saudável.

set -u

STACK_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
ENV_FILE="$STACK_DIR/.env"
COMPOSE_FILE="$STACK_DIR/compose.yaml"
EXAMPLE_PASSWORD="troque-esta-senha"
API_IMAGE="artigo2md-api:local"
SERVICES="postgres rabbitmq api"

say() { printf '%s\n' "$*"; }
fail() { code=$1; shift; printf '%s\n' "$*" >&2; exit "$code"; }

dc() {
    docker compose --project-directory "$STACK_DIR" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

# ── opções ───────────────────────────────────────────────────────────────────────
UPDATE_IMAGES=0
for arg in "$@"; do
    case $arg in
        --atualizar-imagens) UPDATE_IMAGES=1 ;;
        *) fail 2 "Opção não reconhecida. Uso: sh stack/up.sh [--atualizar-imagens]" ;;
    esac
done

# ── passo 1: stack/.env existe ───────────────────────────────────────────────────
[ -f "$ENV_FILE" ] || fail 2 "O arquivo stack/.env não existe. Crie-o com: cp stack/.env.example stack/.env (e preencha as senhas)."

# ── passo 2: leitura do .env (sem eval/source: nada do arquivo é executado) ──────
trim() { printf '%s' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'; }

PAIRS=""
SEEN=""
while IFS= read -r raw || [ -n "$raw" ]; do
    line=$(trim "$raw")
    case $line in
        "" | "#"*) continue ;;
    esac
    case $line in
        "export "* | "export	"*) line=$(trim "${line#export}") ;;
    esac
    case $line in
        *=*) ;;
        *) continue ;;
    esac
    key=$(trim "${line%%=*}")
    value=$(trim "${line#*=}")
    case $value in
        \"*\") value=${value#\"}; value=${value%\"} ;;
        \'*\') value=${value#\'}; value=${value%\'} ;;
    esac
    case $key in
        "" | *[!A-Za-z0-9_]*) continue ;;
    esac
    case "
$SEEN
" in
        *"
$key
"*) fail 2 "A variável $key aparece mais de uma vez em stack/.env. Deixe só uma linha." ;;
    esac
    SEEN="$SEEN
$key"
    PAIRS="$PAIRS
$key=$value"
    case $value in
        *'$'*) fail 2 "O valor de $key em stack/.env contém o caractere \$, que o Compose interpretaria. Remova-o." ;;
    esac
done < "$ENV_FILE"

get_var() {
    printf '%s\n' "$PAIRS" | while IFS= read -r pair; do
        case $pair in
            "$1="*) printf '%s' "${pair#*=}"; break ;;
        esac
    done
}

has_var() {
    case "
$SEEN
" in
        *"
$1
"*) return 0 ;;
    esac
    return 1
}

if has_var POSTGRES_HOST_AUTH_METHOD; then
    fail 2 "POSTGRES_HOST_AUTH_METHOD não pode ser definida em stack/.env (poderia desligar a autenticação do Postgres). Remova a linha."
fi

# ── passo 2b: credenciais ────────────────────────────────────────────────────────
for name in POSTGRES_USER POSTGRES_DB RABBITMQ_DEFAULT_USER; do
    [ -n "$(get_var "$name")" ] || fail 2 "$name está vazia ou ausente: preencha no stack/.env."
done
for name in POSTGRES_PASSWORD RABBITMQ_DEFAULT_PASS; do
    pass=$(get_var "$name")
    [ -n "$pass" ] || fail 2 "$name está vazia ou ausente: preencha no stack/.env."
    [ "$pass" != "$EXAMPLE_PASSWORD" ] || fail 2 "$name ainda tem o valor de exemplo: preencha no stack/.env com uma senha própria."
    [ "${#pass}" -ge 12 ] || fail 2 "$name tem menos de 12 caracteres: preencha no stack/.env com uma senha maior."
done

# ── passo 3: portas ──────────────────────────────────────────────────────────────
for name in ARTIGO2MD_POSTGRES_PORT ARTIGO2MD_RABBITMQ_PORT ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT ARTIGO2MD_API_PORT; do
    port=$(get_var "$name")
    case $port in
        "" | *[!0-9]*) fail 2 "$name precisa ser um número de 1 a 65535 em stack/.env." ;;
    esac
    { [ "$port" -ge 1 ] && [ "$port" -le 65535 ]; } 2>/dev/null || fail 2 "$name precisa ser um número de 1 a 65535 em stack/.env."
done

# ── passo 3b: pasta de dados do app (ARTIGO2MD_DATA_DIR) ─────────────────────────
# Precisa ser um caminho absoluto, de uma pasta existente e já em forma canônica (igual a `pwd -P`):
# a API compara este caminho com o que os workers gravam, e uma diferença quebraria essa comparação.
DATA_DIR=$(get_var ARTIGO2MD_DATA_DIR)
[ -n "$DATA_DIR" ] || fail 2 "ARTIGO2MD_DATA_DIR está vazia ou ausente: preencha no stack/.env com o caminho absoluto da pasta de dados do app (o mesmo de 'artigo2md worker --data-dir')."
case $DATA_DIR in
    /*) ;;
    *) fail 2 "ARTIGO2MD_DATA_DIR precisa ser um caminho absoluto (começando por /) em stack/.env." ;;
esac
[ -e "$DATA_DIR" ] || fail 2 "ARTIGO2MD_DATA_DIR aponta para uma pasta que não existe: crie a pasta ou corrija o caminho em stack/.env."
[ -d "$DATA_DIR" ] || fail 2 "ARTIGO2MD_DATA_DIR precisa ser uma pasta, mas o caminho em stack/.env é de outro tipo de arquivo."
CANONICAL_DATA_DIR=$(CDPATH= cd -- "$DATA_DIR" 2>/dev/null && pwd -P) \
    || fail 2 "ARTIGO2MD_DATA_DIR não pôde ser aberta (permissão negada?): corrija o caminho em stack/.env."
[ "$CANONICAL_DATA_DIR" = "$DATA_DIR" ] \
    || fail 2 "ARTIGO2MD_DATA_DIR não está em forma canônica (tem link simbólico, '..', '.', barra repetida ou final). Use o caminho real: $CANONICAL_DATA_DIR"

# ── passo 4: Docker e Docker Compose ─────────────────────────────────────────────
# A saída de erro original destes comandos é descartada e trocada por mensagem em português.
if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
    fail 3 "Docker não está disponível. Instale o Docker ou abra o Docker Desktop (e espere ele iniciar) e tente de novo."
fi
if ! docker compose version >/dev/null 2>&1; then
    fail 3 "Docker Compose não está disponível. Instale o plugin 'docker compose' (ele acompanha o Docker Desktop) e tente de novo."
fi

RUNNING=$(dc ps --status running --services 2>/dev/null)

is_running() {
    printf '%s\n' "$RUNNING" | grep -qx "$1"
}

if [ "$UPDATE_IMAGES" = 1 ]; then
    for svc in $SERVICES; do
        if is_running "$svc"; then
            fail 2 "A infraestrutura está no ar. Derrube-a com 'sh stack/down.sh' antes de usar --atualizar-imagens."
        fi
    done
fi

PANEL_URL="http://127.0.0.1:$(get_var ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT)/"

# Tudo já rodando: nada a fazer (FR-015).
ALL_RUNNING=1
for svc in $SERVICES; do
    is_running "$svc" || ALL_RUNNING=0
done
if [ "$ALL_RUNNING" = 1 ]; then
    say "A infraestrutura já estava no ar."
    say "Painel do RabbitMQ: $PANEL_URL"
    exit 0
fi

# ── passo 5: porta ocupada (só para serviços que ainda não estão rodando) ────────
port_busy() {
    nc -z -w 1 127.0.0.1 "$1" >/dev/null 2>&1
}

check_ports() {
    svc=$1
    shift
    is_running "$svc" && return 0
    for var in "$@"; do
        port=$(get_var "$var")
        if port_busy "$port"; then
            fail 4 "A porta $port já está em uso (serviço $svc). Troque $var em stack/.env ou libere a porta."
        fi
    done
}

if command -v nc >/dev/null 2>&1; then
    check_ports postgres ARTIGO2MD_POSTGRES_PORT
    check_ports rabbitmq ARTIGO2MD_RABBITMQ_PORT ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT
    check_ports api ARTIGO2MD_API_PORT
else
    say "Aviso: o comando 'nc' não foi encontrado; a verificação de portas ocupadas foi pulada."
fi

# ── passo 6: subir ───────────────────────────────────────────────────────────────
if [ "$UPDATE_IMAGES" = 1 ]; then
    PULL="always"
    BUILD="--build"
elif docker image inspect "$API_IMAGE" >/dev/null 2>&1; then
    PULL="missing"
    BUILD="--no-build"
else
    PULL="missing"
    BUILD="--build"
fi

say "Subindo a infraestrutura (a primeira execução baixa as imagens e pode demorar)..."
# A saída do Docker Compose é exibida sem alteração: é o diagnóstico detalhado.
if ! dc up --detach --wait --wait-timeout 120 --pull "$PULL" "$BUILD"; then
    say "" >&2
    say "A infraestrutura não ficou saudável em até 120 segundos." >&2
    PROBLEMS=""
    for svc in $SERVICES; do
        # Estado e saúde conforme o Compose; serviço que não está "running" e saudável é o problema.
        info=$(dc ps --all --format '{{.Service}} {{.State}} {{.Health}}' 2>/dev/null | grep "^$svc " | head -n 1)
        case $info in
            "$svc running healthy" | "$svc running") ;;
            *) PROBLEMS="$PROBLEMS $svc" ;;
        esac
    done
    [ -n "$PROBLEMS" ] || PROBLEMS=" $SERVICES"
    for svc in $PROBLEMS; do
        say "Serviço com problema: $svc. Veja os registros com: docker compose -p artigo2md logs $svc" >&2
    done
    exit 5
fi

say ""
say "Infraestrutura no ar:"
for svc in $SERVICES; do
    say "  $svc: saudável"
done
say "Painel do RabbitMQ: $PANEL_URL"
exit 0
