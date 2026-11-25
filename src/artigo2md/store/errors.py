"""Hierarquia de exceções da camada de persistência.

Toda mensagem é texto próprio em português. As exceções são sempre lançadas com `raise ... from None`
quando nascem de um erro do driver: a cadeia `__cause__`/`__context__` não pode carregar o texto bruto
do servidor (o campo `detail` pode conter valores de linhas) nem parâmetros de conexão (FR-016, SC-007).
"""
from __future__ import annotations


class StoreError(Exception):
    """Base de todos os erros da camada; a mensagem está sempre em português."""


class StoreConfigError(StoreError):
    """Parâmetro de conexão ausente ou inválido; a mensagem cita só o nome da variável."""


class StoreValidationError(StoreError):
    """Valor fora do domínio aceito (antes ou depois de tocar o banco)."""


class StoreRuleError(StoreError):
    """Recusa de regra de negócio, distinguível de erro de banco."""


class ProjectNameTakenError(StoreRuleError):
    """Já existe projeto ativo com o mesmo nome (sem diferenciar maiúsculas e minúsculas)."""


class ActiveJobExistsError(StoreRuleError):
    """O documento já tem um job pendente ou em andamento."""


class InvalidJobTransitionError(StoreRuleError):
    """Transição de estado recusada, inclusive gravação tardia em job final (notificação repetida)."""


class StoreNotFoundError(StoreError):
    """Registro inexistente."""


class ProjectNotFoundError(StoreNotFoundError):
    """Projeto inexistente."""


class DocumentNotFoundError(StoreNotFoundError):
    """Documento inexistente."""


class JobNotFoundError(StoreNotFoundError):
    """Job inexistente."""


class DatabaseUnavailableError(StoreError):
    """Conexão recusada, caiu ou estourou o prazo; repetir depois de reconectar funciona."""


class StoreDatabaseError(StoreError):
    """Qualquer outro erro do servidor, sem o texto bruto dele."""


class MigrationError(StoreError):
    """Base dos erros do runner de migrações."""


class SchemaTooNewError(MigrationError):
    """O banco registra uma versão mais nova do que a conhecida pelo código."""


class MigrationChecksumError(MigrationError):
    """Migração aplicada alterada depois de aplicada, ou histórico do banco divergente do código."""


class MigrationFailedError(MigrationError):
    """Uma migração falhou e foi desfeita; a mensagem cita o arquivo."""


class MigrationLockTimeoutError(MigrationError):
    """Outro processo segurou o lock de migração além do limite de espera."""
