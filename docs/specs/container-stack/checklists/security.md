# Security Checklist: Infraestrutura local em containers

**Purpose**: validar a qualidade dos requisitos de segurança da `container-stack` (isolamento de rede, credenciais, ausência de telemetria, endurecimento dos containers, licenças) — testa o que está ESCRITO na spec/plan/contracts, não a implementação.
**Created**: 2026-11-24
**Feature**: [spec.md](../spec.md)
**Profundidade / audiência**: Standard; reviewer do PR (defaults da skill — execução autônoma feature-00c, sem operador interativo) [Assumption]

## Isolamento de rede (loopback)

- [x] CHK001 - O requisito de publicação apenas em `127.0.0.1` cobre explicitamente TODAS as portas, inclusive a do painel de gestão do RabbitMQ? [Completude, Spec §FR-006, US2 cenário 1] {auto}
- [x] CHK002 - O requisito impede que o endereço de publicação seja alterado por parâmetro editável (o `.env` só fornece o número da porta)? [Clareza, Spec §FR-006, US2 cenário 4; contracts/stack-scripts.md "Nenhuma variável controla o endereço de publicação"] {auto}
- [x] CHK003 - Estão especificadas as portas internas que NÃO devem ser publicadas (distribuição Erlang/epmd)? [Cobertura, research Decision 5] {auto}
- [x] CHK004 - O critério de "loopback" é mensurável e tem verificação definida (teste estático + `docker compose port` na integração)? [Mensurabilidade, Spec §SC-002; research Decision 10 e Decision 11 item 8] {auto}
- [x] CHK005 - A forma longa de `ports` (sem `host_ip`) e chaves de escape do isolamento (`network_mode`, `privileged`, `docker.sock`) estão proibidas nos requisitos? [Completude, contracts/stack-scripts.md §compose.yaml; research Decision 11 itens 5 e 8] {auto}
- [x] CHK006 - Os requisitos tratam o painel de gestão do RabbitMQ (acessível por navegador em loopback) contra DNS rebinding, como já fazem para a API base com `TrustedHost`? A spec/plan só definem essa proteção para a API. [Gap, contracts/api-health.md §Regras; research Decision 11 item 7] {humano} → ADIADO como risco aceito pelo operador (block-004 item 1); responsável futuro `desktop-stack-control`; research Decision 11 "Riscos aceitos"; tarefa 1.3.1

## Credenciais e parâmetros

- [x] CHK007 - Está definido que o arquivo real de parâmetros nunca é versionado e que só o `.env.example` sem credenciais reais entra no repositório? [Completude, Spec §FR-004, Edge Cases; plan §Project Structure `.gitignore`] {auto}
- [x] CHK008 - Está definido que o arquivo de credenciais não entra no contexto de build da imagem? [Cobertura, research Decision 11 item 4 (`.dockerignore` allowlist)] {auto}
- [x] CHK009 - Os requisitos garantem que mensagens de erro citam só o NOME da variável, nunca o valor? [Clareza, contracts/stack-scripts.md passo 2; research Decision 11 item 1] {auto}
- [x] CHK010 - A política de senha está consistente entre spec e plan? A spec FR-004 só exige "não vazia e diferente do valor de exemplo"; o plan/contrato acrescentam mínimo de 12 caracteres e proibição de `$` sem refletir isso na spec. [Consistência, Spec §FR-004 vs contracts/stack-scripts.md passo 2b e research Decision 11 item 1] {auto} → RESOLVIDO (tarefa fase 1): spec FR-004 agora exige 12+ caracteres, sem `$`, mensagens só com o nome da variável (tarefa 1.1.1)
- [x] CHK011 - Está especificado que parâmetros capazes de desligar a autenticação (`POSTGRES_HOST_AUTH_METHOD`) são recusados? [Cobertura, contracts/stack-scripts.md passo 2; research Decision 11 item 1] {auto}
- [x] CHK012 - Está definido que o `.env` nunca é executado (sem `source`/`eval`) pelos scripts? [Clareza, research Decision 8; data-model.md §Parameters] {auto}
- [x] CHK013 - Os requisitos definem o que acontece com contas padrão do broker/painel além das credenciais fornecidas pelo usuário (existência, desativação)? Spec e plan não tratam disso. [Gap, Spec §FR-004] {auto} → RESOLVIDO (tarefa fase 1): research Decision 11 item 13: requisito definido (não altera `loopback_users`); se o `guest` continua existindo com as variáveis definidas é hipótese não confirmada nas fontes S09b/S11, validada na integração (tarefa 6.1) (tarefa 1.2.1)
- [x] CHK014 - Os requisitos impedem que credenciais apareçam em saídas de teste/log? O teste de integração confere a saída interpolada de `docker compose config` (research Decision 11 item 8) e nenhum requisito diz como evitar que as senhas interpoladas sejam impressas no log do pytest/CI. [Gap, research Decision 11 item 8] {auto} → RESOLVIDO (tarefa fase 1): research Decision 11 item 14: testes e logs não imprimem credenciais (tarefa 1.2.2)
- [x] CHK015 - A exposição de credenciais em `docker inspect` está documentada como risco aceito, com justificativa? [Risco, research Decision 11 "Riscos aceitos"; aceito pelo operador em block-003/dec-023] {humano}

## Ausência de telemetria e de tráfego de saída

- [x] CHK016 - O requisito de "sem telemetria" está definido por configuração E por verificação, sem depender de bloqueio de rede? [Clareza, Spec §FR-007, Clarifications dec-010; research Decision 6] {auto}
- [x] CHK017 - O método de observação de SC-003 ("zero conexões de saída em 10 minutos") está especificado (ferramenta, ponto de captura, quem executa)? A spec só define a métrica. [Mensurabilidade, Gap, Spec §SC-003] {auto} → RESOLVIDO (tarefa fase 1): spec SC-003: verificação manual do mantenedor, captura no host, ferramenta e fonte registradas no PR (tarefa 1.2.3)
- [x] CHK018 - O requisito "baixar imagens só por pedido explícito do usuário" (Edge Cases) é consistente com `up.sh` usar `--pull missing` por padrão, que baixa imagens ausentes na primeira execução sem opção explícita? Definir se rodar `up.sh` pela primeira vez conta como "pedido explícito". [Conflict, Spec §Edge Cases "Máquina sem internet" vs contracts/stack-scripts.md passo 6; plan §Constitution Check I] {auto} → RESOLVIDO (tarefa fase 1): spec Edge Cases "Máquina sem internet" e contracts/stack-scripts.md passo 6: executar o script de subir é o pedido explícito de download (tarefa 1.1.2)
- [x] CHK019 - O caminho de atualização de imagens (rede) está isolado numa opção explícita? [Completude, contracts/stack-scripts.md `--atualizar-imagens`; research Decision 11 item 3] {auto}

## Endurecimento dos containers e supply chain

- [x] CHK020 - Os requisitos de endurecimento do container da API estão especificados (usuário não-root, FS só leitura, `cap_drop`, `no-new-privileges`)? [Completude, research Decision 11 item 5; contracts/stack-scripts.md §compose.yaml] {auto}
- [x] CHK021 - O nível de endurecimento exigido para os containers do Postgres e do RabbitMQ (usuário, FS, capabilities, limites de recurso) é adequado ao apetite de risco? A spec/plan só endurecem a API. [Risco, Gap, research Decision 11] {humano} → RESOLVIDO pelo operador (block-004 item 2): padrões das imagens + APENAS `no-new-privileges`; demais endurecimentos são risco aceito (research Decision 11 item 11 e "Riscos aceitos"; contracts/stack-scripts.md §compose.yaml; tarefas 1.3.2, 4.1.6, 4.4.2b)
- [x] CHK022 - O uso de tags sem digest está registrado como risco aceito, com condição de atualização explícita? [Risco, research Decision 11 "Riscos aceitos"; aceito pelo operador em block-003/dec-023] {humano}
- [x] CHK023 - A ausência de trava contra `up.sh` concorrente e de limite de memória está registrada como risco aceito com responsável futuro (`desktop-stack-control`)? [Risco, research Decision 11 "Riscos aceitos"; aceito pelo operador em block-003/dec-023] {humano}
- [x] CHK024 - A informação exposta pelo `/health` (versão do pacote) está avaliada quanto a sensibilidade? [Cobertura, research Decision 11 "Riscos aceitos"] {auto}

## Proteção de dados e operações destrutivas

- [x] CHK025 - A remoção de dados (volumes) exige opção explícita E confirmação, e o comportamento sem terminal interativo é fail-closed? [Completude, Spec §FR-003, US1 cenário 6; research Decision 11 item 10] {auto}
- [x] CHK026 - O `doctor` está proibido de exibir o texto de erro devolvido pelo servidor (pode conter detalhes internos)? [Clareza, contracts/doctor-infra.md tabela de checagens; research Decision 7] {auto}
- [x] CHK027 - A checagem do `doctor` está definida sem credenciais e sem dependência de proxy do sistema? [Clareza, Spec §FR-009, Clarifications dec-014; research Decision 11 item 6] {auto}

## Licenças (Princípio II) e governança

- [x] CHK028 - Cada serviço/runtime/SO base da stack tem licença com fonte oficial citada e enquadramento no Princípio II? [Completude, Spec §FR-011, §SC-006; research §Registro de licenças] {auto}
- [ ] CHK029 - As licenças das dependências transitivas do grupo `api` estão confirmadas em fonte oficial? Hoje constam como "pendência rastreada" a resolver na implementação. [Gap, research §Registro de licenças linha "fastapi, uvicorn e dependências transitivas"; plan §Constitution Check II] {auto}
- [ ] CHK030 - A interpretação de dec-013 (licença do SO base só documentada, fora do enquadramento) foi ratificada pelo operador por nota/emenda na constitution? [Dependência, Spec §Clarifications dec-013; plan §Constitution Check "Pendência de governança"] {humano} → PENDÊNCIA EXTERNA DO OPERADOR (block-004 item 4): ratificação feita por ele depois; esta feature não edita a constitution; tarefa 1.3.3

## Notes

- Items `{auto}` foram resolvidos contra spec.md, plan.md, research.md, data-model.md e contracts/ (`[x]` com citação, ou `[ ]` com `[Gap]`/`[Ambiguity]`/`[Conflict]`).
- Items `{humano}` CHK015, CHK022 e CHK023 estão `[x]` porque o operador aceitou esses riscos explicitamente em block-003 (dec-023) — não é auto-decisão do agente. CHK006 e CHK021 estão `[x]` por decisão do operador em block-004 (risco aceito / endurecimento mínimo). CHK030 permanece `[ ]` como pendência externa do operador (fora do escopo de edição desta feature).
- Destino dos abertos: CHK010, CHK013, CHK014, CHK017, CHK018 resolvidos na fase 1 de `tasks.md`; CHK029 → tarefa 7.1.2; CHK006, CHK021 → decididos em block-004; CHK030 → pendência externa do operador.
- Nenhum item afirma comportamento de software externo não confirmado em fonte; onde o comportamento depende disso, o item pergunta se o requisito o define.
