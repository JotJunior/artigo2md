# Contract: migrações versionadas `artigo2md.store.migrations`

> **[PROPOSTA — a validar na implementação]**. Runner próprio em SQL puro (decisão do operador, block-005 / dec-024;
> research, Decision 3). Sem subcomando de CLI (spec, Assumptions): aplicado por chamada explícita.

## Arquivos

- Local: `src/artigo2md/store/migrations/NNNN_<slug>.sql` (empacotados com o código; a imagem da API já copia
  `src/artigo2md/` inteiro, `stack/api/Dockerfile`).
- `NNNN` = versão inteira, 4 dígitos, contínua a partir de `0001`; `<slug>` em inglês, `[a-z0-9_]`.
- Primeiro arquivo: `0001_initial.sql` (schema completo do [data-model](../data-model.md)).
- Arquivos são imutáveis depois de publicados: mudança de schema = arquivo novo. Só avanço, sem reversão.
- Comentários no SQL em português; identificadores em inglês.

## Interface

```python
@dataclass(frozen=True)
class MigrationReport:
    applied: tuple[int, ...]     # versões aplicadas nesta chamada (vazio = já atualizado)
    current_version: int

async def apply_migrations(settings: DbSettings) -> MigrationReport: ...
async def apply_migrations_on(connection: asyncpg.Connection) -> MigrationReport: ...   # usado pelos testes
def known_migrations() -> tuple[Migration, ...]: ...   # (version, name, checksum, sql) lidos via importlib.resources
```

## Tabela de controle

```sql
schema_migrations(version integer PRIMARY KEY, name text NOT NULL, checksum text NOT NULL,
                  applied_at timestamptz NOT NULL DEFAULT now())
```

## Comportamento

| Situação | Resultado | Mensagem (pt) | Cenário |
|----------|-----------|---------------|---------|
| Banco vazio | aplica `0001..N` em ordem, cada uma registrada | — | US3-1 |
| Tudo aplicado | `applied=()`; nada muda | relatório "banco já está atualizado (versão N)" | US3-2 |
| Parte aplicada | só as pendentes, em ordem | — | US3-3 |
| Falha no meio de uma migração | rollback daquela migração; versão não registrada; anteriores desta chamada permanecem | `MigrationFailedError`: "A migração 0002_x.sql falhou e foi desfeita: <motivo>" | US3-4 |
| Banco com versão > maior conhecida | recusa antes de aplicar qualquer coisa | `SchemaTooNewError`: "O banco foi criado por uma versão mais nova do artigo2md (versão N); atualize o app." | US3-5 |
| Dois processos simultâneos | o segundo espera o lock; cada migração roda uma vez; nenhum erro | — | US3-6 |
| Checksum diverge de uma aplicada | recusa antes de aplicar qualquer coisa | `MigrationChecksumError`: "A migração 0001_initial.sql foi alterada depois de aplicada." | US3-7 |
| Migração aplicada que não existe mais no código (versão abaixo da maior conhecida, sem arquivo) | recusa antes de aplicar qualquer coisa | `MigrationChecksumError`: "O histórico de migrações do banco diverge do código: a migração 0001 foi aplicada mas não existe mais." | US3-7 |
| Outro processo segura o lock além do limite | desiste sem aplicar nada | `MigrationLockTimeoutError`: "Outro processo está aplicando migrações há mais de 60 s; tente de novo depois que ele terminar." | US3-6 |

`SchemaTooNewError`, `MigrationChecksumError`, `MigrationFailedError` e `MigrationLockTimeoutError` herdam de
`MigrationError(StoreError)`. Todas são lançadas `from None`: `MigrationFailedError` cita o arquivo e um motivo em
texto próprio em português, nunca o texto bruto, o `detail` nem os parâmetros do servidor (FR-016, SC-007).

## Serialização

Sessão: espera pelo lock com `SELECT pg_try_advisory_lock($1)` (chave `bigint` constante do código) repetido a cada
0,2 s até o limite de espera de 60 s [PROPOSTA, constante do módulo]; ao estourar, `MigrationLockTimeoutError`. Obtido o lock →
trabalho → `SELECT pg_advisory_unlock($1)` no `finally`. A conexão de migração usa `timeout` de conexão de 10 s e
`command_timeout=None` (uma migração longa não deve ser cortada pelo prazo de 30 s das operações comuns).
A forma `pg_try_advisory_lock` + repetição foi escolhida por não depender de `lock_timeout` do servidor; confirmar
na implementação (V-03). Cada migração pendente roda em `async with connection.transaction()` (S06),
com o arquivo enviado por `connection.execute(sql)` sem argumentos (S05: "This method can execute many SQL
commands at once, when no arguments are provided"), seguido do `INSERT` em `schema_migrations` na mesma transação.

## Desempenho

SC-001: banco vazio → todas as migrações em até 10 s. O tempo de espera pelo lock de outro processo (até 60 s) não
conta para o SC-001: o teste mede só a execução depois de obtido o lock.
