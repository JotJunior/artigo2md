# Contracts: seção de infraestrutura do `artigo2md doctor`

> Extensão aditiva do contrato existente (`docs/specs/command-line-interface/contracts/cli.md`, FR-012 daquela spec).
> Formato de texto **[PROPOSTA — a validar na implementação]**; o critério de "acessível" vem de block-002/dec-014 e
> os bytes de protocolo vêm de fonte oficial (research, Decision 7).

## Command: `artigo2md doctor`

Sem novas opções. Depois da tabela de motores e da linha "Pronto para uso." / "Faltam motores obrigatórios…", imprime:

```text
Infraestrutura (versão desktop, opcional):
  postgres     127.0.0.1:5432   acessível
  rabbitmq     127.0.0.1:5672   inacessível
  api          127.0.0.1:8778   inacessível
Para subir a infraestrutura: sh stack/up.sh (requer Docker; veja stack/.env.example).
```

- A última linha só aparece quando algum serviço está inacessível.
- Estados possíveis por serviço: `acessível`, `inacessível`, `inacessível (versão de protocolo incompatível)` (só RabbitMQ, quando o servidor devolve o próprio cabeçalho AMQP).

### Checagens (sem credenciais, só biblioteca padrão)

| Serviço | Porta (env → padrão) | Envia | Acessível se |
|---|---|---|---|
| postgres | `ARTIGO2MD_POSTGRES_PORT` → `stack/.env` → 5432 | SSLRequest: Int32 `8`, Int32 `80877103`, em network byte order (research S05, S35) | 1º byte da resposta é `S`, `N` (S04b) ou `E` — `Byte1('E')` da ErrorResponse (S35); o texto do erro nunca é exibido |
| rabbitmq | `ARTIGO2MD_RABBITMQ_PORT` → `stack/.env` → 5672 | 8 octetos `A M Q P 0x00 0x00 0x09 0x01` | resposta começa com octeto de tipo `0x01` (METHOD) e canal `0x0000` (o `Connection.Start`) |
| api | `ARTIGO2MD_API_PORT` → `stack/.env` → 8778 | `GET /health` via `http.client` direto em `127.0.0.1:<porta>` (sem proxy) | HTTP 200 e JSON com `status == "ok"` (`contracts/api-health.md`) |

- Host sempre `127.0.0.1`.
- Resolução da porta (research D11.2): variável de ambiente → arquivo em `ARTIGO2MD_STACK_ENV` → `./stack/.env` → padrão. A seção mostra a origem, ex.: `(portas de stack/.env)`.
- Tempo limite por serviço: 1 s para conectar + 1 s para ler; as três checagens rodam em paralelo; teto total < 5 s (SC-005).
- **Serviço ainda subindo** (alinhado à Clarification dec-014 e ao edge case da spec): "acessível" é só "o handshake responde conforme a tabela". Conexão recusada, sem resposta no tempo limite ou resposta fora do protocolo → `inacessível`. Postgres que responde `E` (ErrorResponse) ao SSLRequest conta como `acessível` (a documentação oficial lida manda tratar o ErrorMessage como resposta possível ao SSLRequest, research S04b; ela NÃO afirma o que o servidor responde durante a inicialização/recuperação, consulta de 2026-11-24 em protocol-flow e server-start). O `doctor` não distingue "respondendo mas ainda não saudável para o Docker" de "saudável": quem mede saúde é o healthcheck do Compose. **Hipótese a validar** no teste de integração (tarefa 6.2.2): como o Postgres responde ao SSLRequest durante a inicialização do volume; o resultado é registrado no research e, se divergir deste texto, o contrato é revisto antes do código.
- Porta inválida em variável de ambiente (não numérica, fora de 1–65535): serviço reportado como `inacessível (porta inválida em <VAR>)`, sem exceção.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | motores obrigatórios instalados — **independente** do estado da infraestrutura |
| 1 | falta motor obrigatório (inalterado) |

### Não-regressão

- `artigo2md convert` não importa o módulo de infraestrutura, não abre socket para nenhum serviço da stack e não imprime nada sobre ela (FR-010).
- Nenhuma dependência nova no `pyproject.toml` para o `doctor`.
