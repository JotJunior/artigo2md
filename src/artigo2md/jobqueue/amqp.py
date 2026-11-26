"""Único ponto de acoplamento com a biblioteca cliente AMQP (`pika`) do worker.

Tudo o que o worker precisa do `pika` passa por aqui: abrir a conexão, montar as propriedades das mensagens e
reconhecer as exceções. Se a `BlockingConnection` (obsoleta desde o pika 1.5.0, que recomenda o adaptador
thread-safe; ver `research.md`, "Verificação da API do pika") for trocada, a mudança fica confinada a
`open_connection` e às suas vizinhas. O `pika` só é importado dentro das funções que o usam, então este módulo
carrega sem o extra `queue`; as exceções são reconhecidas pelo nome na hierarquia da classe, o que permite
testar o slot com o corretor falso sem o `pika` instalado.
"""
from __future__ import annotations

import warnings
from typing import Any

from .schema import CONTENT_TYPE

# O aviso é conhecido e aceito (decisão da tarefa 2.2): a versão do pika é fixada em 1.5.0 e o `tick` mantém
# o heartbeat. Não deve poluir o log do worker.
warnings.filterwarnings("ignore", message=r"BlockingConnection is deprecated", category=DeprecationWarning)

HEARTBEAT_SECONDS = 60
CONNECT_TIMEOUT_SECONDS = 5
VIRTUAL_HOST = "/"  # padrão do broker; o compose não define outro (confirmado na tarefa 6.1.4)

_AUTH_ERRORS = frozenset({"ProbableAuthenticationError", "AuthenticationError", "ProbableAccessDeniedError"})
_PUBLISH_REFUSED = frozenset({"NackError", "UnroutableError"})


def open_connection(settings) -> Any:
    """Abre uma `BlockingConnection` com o broker local (host fixo `infra.HOST`; credenciais de `settings`)."""
    import pika

    parameters = pika.ConnectionParameters(
        host=settings.host,
        port=settings.port,
        virtual_host=VIRTUAL_HOST,
        credentials=pika.PlainCredentials(settings.credentials.user, settings.credentials.password),
        heartbeat=HEARTBEAT_SECONDS,
        connection_attempts=1,
        socket_timeout=CONNECT_TIMEOUT_SECONDS,
    )
    return pika.BlockingConnection(parameters)


def make_properties(headers: dict | None = None, expiration: str | None = None) -> Any:
    """Propriedades de mensagem do contrato: JSON, persistente, com cabeçalhos e expiração opcionais."""
    import pika

    return pika.BasicProperties(
        content_type=CONTENT_TYPE, delivery_mode=2, headers=headers, expiration=expiration
    )


def _class_names(exc: BaseException) -> set[str]:
    return {cls.__name__ for cls in type(exc).__mro__}


def is_amqp_error(exc: BaseException) -> bool:
    return "AMQPError" in _class_names(exc)


def is_auth_error(exc: BaseException) -> bool:
    """Credenciais (ou acesso ao vhost) recusadas; o broker real decide qual exceção chega (tarefa 6.1.4)."""
    return bool(_class_names(exc) & _AUTH_ERRORS)


def is_publish_refused(exc: BaseException) -> bool:
    """Publicação não confirmada (`Nack`) ou não roteável (devolvida por `mandatory`)."""
    return bool(_class_names(exc) & _PUBLISH_REFUSED)


def is_connection_problem(exc: BaseException) -> bool:
    """Falha que se resolve reconectando: erro AMQP (conexão, canal, publicação) ou de socket."""
    return isinstance(exc, OSError) or is_amqp_error(exc)
