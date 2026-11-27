# Quickstart: desktop-stack-control

Cenários de validação. Os automáticos rodam sem Docker (`uv run pytest`, `cargo test` em
`desktop/src-tauri`, `npm test` em `desktop/`); os marcados **[integração]** exigem o compose e ficam
atrás de `ARTIGO2MD_STACK=1` (Princípio IV; mesma variável de `tests/test_stack_integration.py`,
`README.md:204`). Testes de scripts e do Docker usam executáveis falsos no `PATH`, como
`tests/test_stack_scripts.py:20-129`.

## Pré-requisitos

1. Repositório clonado com `uv sync --extra dev --extra queue` (CLAUDE.md, "Quando o usuário pedir para
   instalar").
2. App aberto com `npm run tauri dev` em `desktop/` (scripts de `desktop/package.json`).
3. Em Configurações, indicar a pasta do repositório.

## Cenário 1: Diagnóstico do runtime (US1)

1. Com `PATH` de teste sem `docker` → abrir o app.
2. Instalar/iniciar um runtime fora do app → "Verificar novamente".

**Expected**: passo 1 mostra "nenhum runtime Docker encontrado" e o guia; passo 2 mostra "Docker
pronto" com o runtime em uso e libera "Iniciar infraestrutura". "Arquivos" funciona nos dois.

Erro: `docker` falso cujo `compose version` sai com 1 → "Docker Compose ausente"; iniciar bloqueado.

## Cenário 2: Primeira subida com parâmetros gerados (US2, US6) [integração]

1. Sem `stack/.env` e sem imagens → "Iniciar infraestrutura".
2. Na lista da primeira execução, confirmar "imagens da infraestrutura".
3. Ao concluir, "Iniciar infraestrutura".

**Expected**: passo 1 não chama o `up.sh` nem baixa nada: o `start_stack` devolve `imagesMissing` e a tela
mostra a lista da primeira execução com tamanho aproximado e pedido de confirmação (US2-1, US6-1, FR-031); passo 2 mostra progresso atualizado em ≤ 5 s; passo 3 cria `stack/.env` com modo `0600`,
senhas de 32 caracteres hexadecimais e sobe os três serviços até "saudável". `ps -axo command`, o
registro do app (`artigo2md.log`) e a tela não contêm as senhas. `docker compose -p artigo2md port
rabbitmq 15672` não devolve endereço (painel não publicado, FR-029; a forma exata da saída sem porta
publicada é a validar na implementação), e o painel da tela não mostra endereço de painel de gestão.

Erro: porta 5432 ocupada por outro processo → "A porta 5432 do Postgres está em uso"; nada do outro
processo muda; trocar a porta nas configurações avançadas e tentar de novo sobe a stack.

## Cenário 3: Idempotência e parada (US2)

1. Com a stack no ar → "Iniciar infraestrutura" → "Parar infraestrutura" → "Parar infraestrutura".

**Expected**: "já estava no ar"; containers removidos e volumes `pgdata`/`rabbitmqdata` mantidos
(`stack/compose.yaml:98-100`); "já estava parada", sem erro.

## Cenário 4: Pool de workers e parada calma (US3) [integração]

1. Stack no ar → "Iniciar workers" → enfileirar um job longo pela API → "Parar workers".

**Expected**: painel mostra o pool em execução com as mensagens do worker; ao parar, "Encerrando
workers…", o job volta à fila, e em até 60 s `pgrep -g <pgid>` não lista nenhum processo.

Erro: `.env` com senha do RabbitMQ diferente da do volume → pool "parado de forma inesperada" com o
motivo "credenciais recusadas" (código 4), sem reinício em laço, com "Reiniciar".

## Cenário 5: Fechar o app com pool ativo (US3, FR-024) [integração]

1. Pool em execução → fechar a janela.

**Expected**: "Encerrando workers…"; o app termina só depois do grupo vazio, exceto se o teto de 60 s do
fechamento (SC-005, `plan.md` M21) for atingido: nesse caso o app sai, registra os PIDs remanescentes e a
próxima abertura aplica o cenário 6 (pool órfão); Postgres, RabbitMQ e API continuam saudáveis (`artigo2md
doctor`).

## Cenário 6: Pool órfão (US3, FR-021)

1. Pool em execução → matar o app com `kill -9` → abrir o app.

**Expected**: o app oferece "Parar" ou "Continuar usando" o pool anterior e não inicia outro. Um
`artigo2md worker` iniciado à mão no terminal não aparece nem é encerrado.

## Cenário 7: Preparar modelos (US6, FR-036)

1. `HF_HUB_CACHE` apontando para pasta vazia → app mostra "modelos de conversão: pendente".
2. "Preparar modelos" → confirmar.
3. Fechar o app no meio → reabrir.

**Expected**: nada é baixado antes da confirmação; o progresso em bytes aparece a cada ≤ 5 s; após
reabrir, a etapa não está "concluída" e "Preparar modelos" é oferecido de novo; ao concluir,
`artigo2md models status --json` mostra os dois modelos `present: true` e uma conversão com a internet
desligada funciona.

Erro: rede desligada no passo 2 → etapa "falhou", motivo "sem internet", "Tentar de novo".

Fechamento: no passo 3, ao reabrir, `pgrep -f 'artigo2md models'` não lista nenhum processo (a
operação foi encerrada junto com o app) e "Iniciar workers" é recusado com "modelos ainda não
preparados".

Erro de versão (FR-016A): repositório sem o subcomando `models` (saída 2) ou com evento sem `schema_version`
→ mensagem de repositório incompatível, nenhuma operação executada.

Teste automático: `artigo2md models status --json` com cache vazio emite `present: false` e sai com 0;
`download` com `snapshot_download` substituído por falso emite `plan`, `disk`, `progress`, `result`
nessa ordem.

## Cenário 8: Roundtrip End-to-End (borda núcleo ↔ API ↔ interface) [integração]

1. Stack no ar, worker parado.
2. Teste Rust de integração chama a sondagem real de `GET /v1/status` em `127.0.0.1:<ARTIGO2MD_API_PORT>`
   e desserializa a resposta com o mesmo tipo usado em produção.
3. O mesmo teste serializa `InfraSnapshot` e o compara com o esquema de `data-model.md`; o teste npm
   valida um `InfraSnapshot` serializado pelo próprio Rust (fixture gerada por um teste `cargo test`
   que roda sem Docker e versionada em `desktop/tests/fixtures/`, não escrita à mão), para que o npm
   continue rodando sem Docker.

Gate: os testes Rust que precisam da stack checam `ARTIGO2MD_STACK=1` no início e retornam cedo (com
aviso) quando ausente, como os testes Python atrás da mesma variável.

**Expected**: `consumer.connected` presente e booleano no payload real; `consumerConnected` do
retrato igual a ele; chaves do retrato em camelCase iguais às do `types.ts`.

## Cenário 9: Só loopback e CLI intacta (US7)

1. Sessão completa (diagnóstico, subir, workers, painel, parar) com `nettop -p <pid do app>` ou
   `lsof -a -i -p <pid do app>` observando.
2. `uv run artigo2md convert`, `doctor` e `serve` sem Docker.

**Expected**: todas as conexões do processo do app têm destino `127.0.0.1`; CSP de
`tauri.conf.json` inalterada (`desktop/tests/security-config.test.ts` passa sem mudança); comandos da
CLI com o mesmo comportamento e a suíte `uv run pytest` verde.
