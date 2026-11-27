# Contrato: `artigo2md worker`

**[PROPOSTA — comando novo desta feature]**. Mensagens em português (FR-018); nomes de opções em inglês.

```text
artigo2md worker --data-dir PASTA --workers N --min-free-memory MB
                 [--retry-delay SEG] [--max-attempts N] [--idle-timeout SEG] [--shutdown-timeout SEG]
                 [--conversion-timeout SEG]
```

| Opção | Tipo | Padrão | Validação |
|-------|------|--------|-----------|
| `--data-dir` | caminho | **obrigatória** | existe, é diretório, gravável; resolvida na inicialização |
| `--workers` | int | 4 (o maior pool medido; derivado em [`ram-measurement.md`](../ram-measurement.md)) | `>= 1` |
| `--min-free-memory` | int (MB) | 4822 (derivado em [`ram-measurement.md`](../ram-measurement.md)) | `>= 0` |
| `--retry-delay` | float (s) | 30 | `> 0` |
| `--max-attempts` | int | 3 | `>= 1` (1 = sem retry) |
| `--idle-timeout` | float (s) | 300 | `>= 0` (0 = nunca descarrega, como no `serve`) |
| `--shutdown-timeout` | float (s) | 30 | `>= 0` |
| `--conversion-timeout` | float (s) | 0 (sem limite) | `0` (sem limite) ou `> 0` e `< 1800`; valor `>= 1800` é recusado com código 2 (prazo do broker de 30 min: `consumer_timeout` do RabbitMQ 4.4.1, `research.md` Decision 11); padrão `0` para não fixar número sem medição (FR-025) |

**Padrões de `--workers` e `--min-free-memory`** (tarefa 7.3): vêm da medição de RAM feita num Mac real
(`docs/specs/conversion-queue/ram-measurement.md`, gerado de `ram-measurement.raw.json`), pela regra de
`research.md` Decision 8; a seção "Derivação dos padrões" do relatório cita os números medidos e as constantes.
Antes de existir o relatório, as duas opções eram obrigatórias e a ausência era recusada com "medição pendente"
(código de saída 2); essa recusa deixou de valer. Os padrões não extrapolam além do maior pool medido e valem para
a máquina da medição (64 GiB); em máquinas com menos memória, o aviso "pool acima da RAM" e a proteção de RAM
(FR-005) adiam conversores em vez de recusar o valor. Alterar os padrões exige refazer a medição.

**Limite por conversão e prazo do broker** (tarefa 1.6): o slot mantém o heartbeat da conexão a cada `tick`
enquanto espera o filho. O RabbitMQ 4.4.1 (imagem `rabbitmq:4.4-management`) tem prazo de confirmação de
entrega ao consumidor `consumer_timeout` = 1800000 ms (30 minutos) por padrão (ver `research.md`, Decision 11);
passado o prazo sem `ack`, o broker fecha o canal. Por isso `--conversion-timeout`, quando usado, deve ser
**menor** que o prazo do broker. O padrão `0` não impõe limite do lado do worker; um job que passe de 30
minutos sem `ack` perde o canal e é reentregue (at-least-once).

Não há opção de host: o endereço é `127.0.0.1` (FR-016). Porta e credenciais: `contracts/queue-messages.md`
não as cita; ver research.md Decision 9.

## Códigos de saída

| Código | Quando |
|--------|--------|
| 0 | encerrado por SIGINT/SIGTERM após drenar/devolver o job em andamento |
| 1 | defeito inesperado do worker (`EXIT_FAILURE` em `settings.py`) |
| 2 | erro de uso (opção ausente/inválida, `--data-dir` inexistente) — padrão do `argparse` |
| 3 | extra `queue` não instalado (`pika`/`psutil` ausentes) |
| 4 | credenciais ausentes ou recusadas pelo broker |
| 5 | topologia existente incompatível |

Broker fora do ar **não** é saída com erro: o worker avisa e tenta de novo (FR-014).

## Mensagens (texto proposto)

| Situação | Mensagem |
|----------|----------|
| `--data-dir` ausente | erro do `argparse` em português: `a opção --data-dir é obrigatória (pasta onde o worker grava o Markdown)` |
| extra ausente | `O worker precisa das dependências da fila. Instale com: uv sync --extra queue` |
| broker inacessível | `RabbitMQ inacessível em 127.0.0.1:<porta>. Para subir a infraestrutura: sh stack/up.sh (requer Docker; veja stack/.env.example). Nova tentativa em <s> s.` |
| credenciais ausentes | `Credenciais do RabbitMQ não encontradas. Defina RABBITMQ_DEFAULT_USER e RABBITMQ_DEFAULT_PASS no ambiente ou em stack/.env.` (nunca o valor) |
| credenciais recusadas | `O RabbitMQ recusou as credenciais. Revise RABBITMQ_DEFAULT_USER e RABBITMQ_DEFAULT_PASS (origem: <ambiente\|arquivo>).` |
| memória insuficiente | `Memória livre insuficiente para iniciar mais um conversor (<livre> MB livres, mínimo <min> MB). Nova verificação em 10 s.` |
| pool acima da RAM | `Aviso: <N> conversores podem esgotar a memória deste computador; a proteção de RAM vai adiar conversores quando faltar memória.` |
| topologia incompatível | `A fila/exchange <nome> já existe no RabbitMQ com configuração diferente. Nada foi alterado. Remova-a pelo painel (abra-o com 'sh stack/up.sh --management-panel'; ele fica sem proteção contra DNS rebinding até um 'sh stack/up.sh' sem a opção ou um 'sh stack/down.sh') ou pela linha de comando do broker, ou alinhe a configuração.` |
| tempo de conversão excedido | `A conversão do job <job_id> excedeu o limite de <s> s e foi encerrada.` (código `conversion_timeout`, passageira) |
| encerramento | `Encerrando: aguardando o job em andamento por até <s> s...` / `Job <job_id> devolvido à fila.` |

A linha `Para subir a infraestrutura: ...` repete o texto do `doctor` (fonte: `src/artigo2md/infra.py`,
`format_section`).
