# API Checklist: Controle da infraestrutura pelo app desktop

**Purpose**: Validar a qualidade dos requisitos dos contratos desta feature: comandos Tauri (IPC), subcomando `artigo2md models`, códigos de saída de processos externos e ajustes na `container-stack`.
**Created**: 2026-11-26
**Feature**: [spec.md](../spec.md)

## Contratos IPC e de processos

- [x] CHK001 - Todo comando Tauri novo tem request, response e erros definidos e rotulados como proposta? [Completude, contracts/tauri-commands.md (15 comandos), Plan Constitution Check V] {auto}
- [x] CHK002 - Os códigos de saída do `up.sh` (1 a 5) mapeiam para códigos de erro do IPC com mensagem própria em português? [Completude, Spec §FR-014; contracts/external-processes.md `up.sh`; tauri-commands.md "Error Responses"] {auto}
- [x] CHK003 - O mapeamento do `down.sh` cobre todos os códigos possíveis? O exit 2 é declarado "não ocorre" e não há linha na tabela de erros para código não documentado do `down.sh` (`scriptFailed` cita só o `up.sh`). [Gap, Spec §FR-014; contracts/external-processes.md `down.sh`; tauri-commands.md "Error Responses"] {auto} — Resolvido (feature-00c, tarefa FASE 1, 2026-11-27): contracts/tauri-commands.md 'Error Responses' (mapeamento completo do `down.sh`); contracts/external-processes.md.
- [x] CHK004 - Os códigos de saída do worker (0 a 5) mapeiam para motivos próprios traduzidos? [Completude, Spec §FR-014, §FR-022, Edge "Código de saída do worker 3, 4 ou 5"; contracts/external-processes.md `artigo2md worker`] {auto}
- [x] CHK005 - As convenções de case e de validação por camada (IPC camelCase, JSON Lines snake_case, `.env` UPPER_SNAKE) estão definidas sem conflito? [Consistência, Plan "Convenções de Borda"] {auto}
- [x] CHK006 - A idempotência de iniciar e parar tem resposta contratual distinguível (`alreadyInState`)? [Clareza, Spec §FR-009; contracts/tauri-commands.md `start_stack / stop_stack`] {auto}
- [x] CHK007 - O contrato de `artigo2md models` define eventos, campos, tipos, códigos de saída e comportamento sem rede (`status`) e com rede (`download`)? [Completude, contracts/models-cli.md; Spec §FR-036(a,b,c)] {auto}
- [x] CHK008 - O consumo de `GET /v1/status` para o aviso "consumidor de eventos parado" tem fonte de contrato registrada? [Rastreabilidade, Spec §FR-028; Plan "Convenções de Borda" (`project-api/contracts/http-api.md:170-176`)] {auto}
- [x] CHK009 - O contrato IPC garante que nenhuma credencial transita e que o detalhe de erro só vai ao registro do app? [Segurança de contrato, contracts/tauri-commands.md (cabeçalho); Spec §FR-011] {auto}
- [x] CHK010 - O estado devolvido à interface (`InfraSnapshot`) cobre todas as entidades da spec (runtime, repositório, serviços, pool, primeira execução, operação)? [Completude, data-model.md "Entity: InfraSnapshot"; Spec "Key Entities"] {auto}

## Versionamento e dependências entre componentes

- [x] CHK011 - Há requisito para o caso de a pasta do repositório indicada ter versão diferente da do app (por exemplo, sem o subcomando `artigo2md models`, sem o `--management-panel` ou com contrato de `models` diverso)? O app não empacota o repositório (FR-016) e os eventos de `models` não trazem campo de versão (o contrato do worker traz `schema_version`). [Gap, Spec §FR-016, §FR-036; contracts/models-cli.md] {auto} — Resolvido (feature-00c, tarefa FASE 1, 2026-11-27): spec.md FR-016A; contracts/models-cli.md (`schema_version`); data-model.md `RepoCheck.modelsUnsupported`.
- [x] CHK012 - O impacto em `container-stack` (painel do RabbitMQ fora da publicação padrão) está inventariado por artefato, linha e ajuste, e registrado como requisito modificado? [Rastreabilidade, Spec "Delta Requirements" (MODIFIED), Spec §FR-029; Plan tabela "Impacto nos artefatos e testes da container-stack"] {auto}
- [x] CHK013 - Cada linha da tabela de impacto na `container-stack` (spec, contratos, quickstart, research, data-model, checklist de segurança, testes, README) tem tarefa correspondente? A própria tabela declara "vira tarefa no create-tasks". [Gap, Plan tabela "Impacto nos artefatos e testes da container-stack"] {auto} — Resolvido (feature-00c, tarefa FASE 1, 2026-11-27): plan.md, 'Rastreio da tabela de impacto até as tarefas' (tarefas 2.1 a 2.7 e 3.1 a 3.8).
- [x] CHK014 - Cada ponto marcado "a validar na implementação" tem um dono verificável (tarefa ou teste)? Pontos: bytes no `--progress json`; Compose para colima; `proc_listpids`/`proc_pidpath` no `libc` 0.2.191; saída de `docker compose port rabbitmq 15672` sem publicação; recriação do container pelo `up` ao perder a porta; remoção pelo `down` do container iniciado com o arquivo adicional; dono do socket em escuta (M2). [Gap, Plan "Riscos e pontos a validar", M2, M8] {auto} — Resolvido (feature-00c, tarefa FASE 1, 2026-11-27): plan.md, 'Dono verificável de cada ponto a validar' (tarefas 11.1 a 11.5).

## Confirmação e erros

- [x] CHK015 - O requisito de confirmação explícita antes de qualquer download tem mecanismo contratual (diálogo nativo no Rust; `confirmed` apenas UX) e erro definido (`confirmationRequired`)? [Clareza, Spec §FR-031; contracts/tauri-commands.md `prepare_images / prepare_models`; Plan M16] {auto}
- [x] CHK016 - As falhas de download têm motivos enumerados (`no_network`, `disk_full`, `interrupted`, `other`) e mapeamento para `FirstRunStep.failure`? [Completude, Spec §FR-034; contracts/models-cli.md; tauri-commands.md `prepare_models`] {auto}

## Notes

- Items `{auto}` resolvidos pelo agente; os abertos acima são `[Gap]` e viram tarefas no create-tasks (CHK003, CHK011, CHK013, CHK014)
- Não há items `{humano}` neste domínio
