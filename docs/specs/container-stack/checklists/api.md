# API Checklist: Infraestrutura local em containers

**Purpose**: validar a qualidade dos requisitos de interface da `container-stack` — contrato `GET /health` da API base, seção de infraestrutura do `artigo2md doctor` e contrato dos scripts `up.sh`/`down.sh` — testando o que está ESCRITO, não a implementação.
**Created**: 2026-11-24
**Feature**: [spec.md](../spec.md)
**Profundidade / audiência**: Standard; reviewer do PR (defaults da skill — execução autônoma feature-00c, sem operador interativo) [Assumption]

## Contrato `GET /health`

- [x] CHK001 - O payload de resposta está definido campo a campo (nome, tipo, valor) com exemplo? [Completude, contracts/api-health.md §Response (200)] {auto}
- [x] CHK002 - A origem do valor de `version` está rastreada até o código? [Clareza, contracts/api-health.md (`artigo2md.__version__`, `src/artigo2md/__init__.py:2`)] {auto}
- [x] CHK003 - O comportamento para qualquer rota diferente de `/health` (incluindo `/docs`, `/redoc`, `/openapi.json`) está especificado? [Cobertura, Spec §FR-008, US4 cenário 2; contracts/api-health.md §Error Responses e §Regras] {auto}
- [x] CHK004 - O contrato define a resposta para métodos diferentes de GET em `/health` (ex.: POST, HEAD)? Só GET e "outra rota" estão cobertos. [Gap, contracts/api-health.md] {auto} → RESOLVIDO (tarefa fase 1): contracts/api-health.md §Error Responses: 405 para métodos ≠ GET, observado e fixado por teste (tarefa 1.2.4)
- [x] CHK005 - O contrato deixa claro se a validação de `Host` (`allowed_hosts=["127.0.0.1", "localhost"]`) aceita o header com porta (`127.0.0.1:<porta>`) enviado pelo `doctor` e pelo healthcheck interno? O research afirma que o healthcheck "passa no filtro" sem fonte que trate do caso com porta. [Ambiguity, contracts/api-health.md §Regras; research Decision 11 item 7] {auto} → RESOLVIDO (tarefa fase 1): contracts/api-health.md §Regras "`Host` com porta": aceito, fonte o código instalado do Starlette + observação (tarefa 1.1.4)
- [x] CHK006 - A convenção de nomes das chaves (inglês, `snake_case`) está definida? [Consistência, contracts/api-health.md §Regras; plan §Convencoes de Borda] {auto}
- [x] CHK007 - O requisito de não carregar motores pesados no processo da API é verificável objetivamente? [Mensurabilidade, Spec §FR-008, US4 cenário 3; contracts/api-health.md (teste em subprocesso de `sys.modules`)] {auto}
- [x] CHK008 - Existe estratégia de versionamento/compatibilidade do contrato para quando a `project-api` acrescentar rotas (o `/health` e seu payload permanecem estáveis)? [Gap, Spec §US4; contracts/api-health.md cabeçalho] {humano} → DEFINIDO nesta feature por decisão do operador (block-004 item 3): contracts/api-health.md §Versionamento e compatibilidade; teste na tarefa 2.2.6

## Seção de infraestrutura do `doctor`

- [x] CHK009 - O critério de "acessível" está definido por serviço, com os bytes enviados e a resposta esperada, rastreados a fonte oficial? [Clareza, Spec §FR-009, Clarifications dec-014; contracts/doctor-infra.md §Checagens; research Decision 7] {auto}
- [x] CHK010 - Todos os estados possíveis por serviço estão enumerados e consistentes entre contrato e data model? [Consistência, contracts/doctor-infra.md (estados + porta inválida) vs data-model.md §InfraReport `state`] {auto}
- [x] CHK011 - "Poucos segundos" (FR-009) está quantificado com tempos limite por serviço e teto total? [Clareza, Spec §SC-005; contracts/doctor-infra.md (1 s + 1 s, paralelo, < 5 s)] {auto}
- [x] CHK012 - O código de saída do `doctor` está explicitamente desacoplado do estado da infraestrutura? [Completude, Spec §FR-009, US3 cenário 2; contracts/doctor-infra.md §Exit codes] {auto}
- [x] CHK013 - A ordem de resolução das portas e a exibição da origem estão especificadas? [Completude, contracts/doctor-infra.md; research Decision 11 item 2] {auto}
- [x] CHK014 - O estado parcial (só alguns serviços no ar) está coberto, com reporte individual? [Cobertura, Spec §US3 cenário 5] {auto}
- [x] CHK015 - O estado reportado para um servidor que responde ao handshake com ErrorResponse durante a inicialização é consistente com o edge case "serviços ainda subindo → inacessível"? O contrato classifica a resposta `E` do Postgres como `acessível` sem distinguir o caso de inicialização. [Conflict, Spec §Edge Cases (`doctor` em estado intermediário) vs contracts/doctor-infra.md §Checagens linha postgres] {auto} → RESOLVIDO (tarefa fase 1): contracts/doctor-infra.md "Serviço ainda subindo" + spec Edge Cases: acessível = responde ao handshake; comportamento do Postgres na inicialização é hipótese para 6.2.2 (tarefa 1.1.3)
- [x] CHK016 - A não-regressão do `convert` (sem import, sem socket, sem mensagem sobre a stack) está especificada e verificável? [Completude, Spec §FR-010, §SC-005; contracts/doctor-infra.md §Não-regressão; plan §Ordem item 2] {auto}
- [x] CHK017 - O formato de texto da seção está definido (exemplo, condição da linha de instrução)? [Clareza, contracts/doctor-infra.md §Command (marcado [PROPOSTA])] {auto}

## Contrato dos scripts `up.sh` / `down.sh`

- [x] CHK018 - Os códigos de saída de cada falha do `up.sh` estão enumerados de forma distinta e na ordem de validação? [Completude, contracts/stack-scripts.md tabela do `up.sh` (2/3/4/5)] {auto}
- [x] CHK019 - A reexecução sobre stack já no ar está especificada (mensagem + exit 0, sem chamar `up`) e a stack parcial também? [Cobertura, Spec §FR-015, US1 cenário 4; research Decision 8] {auto}
- [x] CHK020 - Está definido o comportamento quando o `docker` responde mas o subcomando `docker compose` não está disponível? O passo 4 só testa `docker info`. [Gap, Spec §FR-012; contracts/stack-scripts.md passo 4] {auto} → RESOLVIDO (tarefa fase 1): contracts/stack-scripts.md passo 4: `docker compose version`, exit 3 (tarefa 1.2.5)
- [x] CHK021 - Está definido se a saída original do Docker/Compose (em inglês) é exibida, suprimida ou acompanhada de explicação em português? O edge case proíbe "mensagem em inglês do cliente do Docker sem explicação", mas o contrato não diz o que acontece com a saída do `compose up`. [Ambiguity, Spec §Edge Cases (Docker ausente), §FR-012; contracts/stack-scripts.md] {auto} → RESOLVIDO (tarefa fase 1): spec Edge Cases e contracts/stack-scripts.md: saída do Compose exibida sem alteração, cercada de mensagens em português (tarefa 1.1.5)
- [x] CHK022 - SC-001 ("imagens já baixadas", até 2 min) define se a imagem local da API precisa estar construída antes da medição, já que o `up.sh` faz `--build` quando `artigo2md-api:local` falta? [Ambiguity, Spec §SC-001; contracts/stack-scripts.md passo 6; research Decision 11 item 3] {auto} → RESOLVIDO (tarefa fase 1): spec SC-001: pressupõe imagem da API já construída (tarefa 1.1.6)
- [x] CHK023 - O estado da stack após falha de saúde está definido (containers mantidos para inspeção, comando de logs informado)? [Completude, Spec §Edge Cases (serviço não saudável); data-model.md §Stack state transitions; contracts/stack-scripts.md passo 6] {auto}
- [x] CHK024 - O `down.sh` define comportamento com `.env` ausente ou quebrado? [Cobertura, contracts/stack-scripts.md §down.sh] {auto}
- [x] CHK025 - Combinações de opções não previstas estão especificadas (ex.: `--sim` sem `--apagar-dados`, opção desconhecida, `--atualizar-imagens` com stack já no ar)? [Gap, contracts/stack-scripts.md] {auto} → RESOLVIDO (tarefa fase 1): contracts/stack-scripts.md §Opções e §down.sh: opção desconhecida e `--sim` sozinho → exit 2; `--atualizar-imagens` com stack no ar → exit 2 (tarefa 1.2.6)

## Notes

- Items `{auto}` foram resolvidos contra spec.md, plan.md, research.md, data-model.md e contracts/ (`[x]` com citação, ou `[ ]` com `[Gap]`/`[Ambiguity]`/`[Conflict]`).
- Todos os itens abertos foram resolvidos na fase 1 de `tasks.md` (CHK005, CHK015, CHK021, CHK022 por ajuste de spec/contratos; CHK004, CHK020, CHK025 por definição nos contratos, com testes nas fases 2 e 5); CHK008 decidido em block-004.
- Gate `requirement-coverage.sh` sobre spec.md: `requirements=15 covered=15 errors=0` (nenhum FR sem cenário).
