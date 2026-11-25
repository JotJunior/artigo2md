#!/bin/sh
# Derruba a infraestrutura local do artigo2md.
#
# Uso:  sh stack/down.sh [--apagar-dados [--sim]]
#
#   (sem opção)      remove os containers e a rede; os dados (volumes) são MANTIDOS.
#   --apagar-dados   remove também os volumes (apaga os dados do Postgres e do RabbitMQ). Pede que
#                    você digite "apagar". Sem terminal interativo, não apaga nada (falha fechado).
#   --sim            só vale junto com --apagar-dados: confirma sem perguntar (para automação).
#
# Não exige um stack/.env completo (para conseguir derrubar mesmo com o arquivo quebrado): se ele
# não existir, usa stack/.env.example só para a interpolação do Compose.
#
# Códigos de saída: 0 ok | 1 exclusão de dados não confirmada | 2 opções inválidas | 3 Docker indisponível.

set -u

STACK_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
COMPOSE_FILE="$STACK_DIR/compose.yaml"
if [ -f "$STACK_DIR/.env" ]; then
    ENV_FILE="$STACK_DIR/.env"
else
    ENV_FILE="$STACK_DIR/.env.example"
fi

say() { printf '%s\n' "$*"; }
fail() { code=$1; shift; printf '%s\n' "$*" >&2; exit "$code"; }

dc() {
    docker compose --project-directory "$STACK_DIR" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

USAGE="Uso: sh stack/down.sh [--apagar-dados [--sim]]"

WIPE=0
YES=0
for arg in "$@"; do
    case $arg in
        --apagar-dados) WIPE=1 ;;
        --sim) YES=1 ;;
        *) fail 2 "Opção não reconhecida. $USAGE" ;;
    esac
done
if [ "$YES" = 1 ] && [ "$WIPE" = 0 ]; then
    fail 2 "A opção --sim só vale junto com --apagar-dados. $USAGE"
fi

# A saída de erro original dos comandos de checagem é descartada e trocada por mensagem em português.
if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
    fail 3 "Docker não está disponível. Instale o Docker ou abra o Docker Desktop (e espere ele iniciar) e tente de novo."
fi
if ! docker compose version >/dev/null 2>&1; then
    fail 3 "Docker Compose não está disponível. Instale o plugin 'docker compose' (ele acompanha o Docker Desktop) e tente de novo."
fi

if [ "$WIPE" = 1 ]; then
    if [ "$YES" = 0 ]; then
        if [ ! -t 0 ]; then
            fail 1 "Sem terminal interativo: nada foi apagado. Para apagar os dados sem perguntar, use --apagar-dados --sim."
        fi
        say "Isto apaga TODOS os dados do Postgres e do RabbitMQ (volumes pgdata e rabbitmqdata)."
        printf 'Para confirmar, digite apagar: '
        answer=""
        read -r answer || answer=""
        if [ "$answer" != "apagar" ]; then
            fail 1 "Confirmação não recebida: nada foi apagado."
        fi
    fi
    say "Derrubando a infraestrutura e apagando os dados..."
    dc down --volumes || fail 1 "O Docker Compose não conseguiu derrubar a infraestrutura."
    say "Infraestrutura derrubada. Os dados foram apagados."
    exit 0
fi

say "Derrubando a infraestrutura (os dados são mantidos)..."
dc down || fail 1 "O Docker Compose não conseguiu derrubar a infraestrutura."
say "Infraestrutura derrubada. Os dados (volumes pgdata e rabbitmqdata) foram mantidos."
exit 0
