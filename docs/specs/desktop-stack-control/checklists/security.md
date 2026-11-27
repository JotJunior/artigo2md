# Security Checklist: Controle da infraestrutura pelo app desktop

**Purpose**: Validar a qualidade dos requisitos de segurança e privacidade (credenciais, confinamento em loopback, execução de processos, arquivos e downloads).
**Created**: 2026-11-26
**Feature**: [spec.md](../spec.md)

## Credenciais

- [x] CHK001 - Está especificado onde as credenciais NÃO podem aparecer (tela, mensagens, registros, detalhes técnicos, linha de comando) e como chegam aos workers (ambiente do processo)? [Clareza, Spec §FR-011, §SC-007; Plan M5, M10] {auto}
- [x] CHK002 - A geração das credenciais tem requisito mensurável (aleatória, 12+ caracteres, sem `$`, diferente do exemplo, arquivo com permissão restrita) e a fonte de aleatoriedade está definida? [Mensurabilidade, Spec §FR-010; data-model.md StackParams (32 hex de `/dev/urandom`)] {auto}
- [x] CHK003 - Há requisito para nunca sobrescrever nem regenerar credenciais existentes e para tratar `.env` inválido sem exibir valor? [Cobertura, Spec §FR-010, Edge "Arquivo de parâmetros existente mas inválido"; Plan M9] {auto}
- [x] CHK004 - O requisito de não vazamento é verificável de forma objetiva (procurar a senha gerada em toda saída capturada)? [Mensurabilidade, Spec §SC-007; Plan M10] {auto}

## Confinamento de rede e downloads

- [x] CHK005 - O confinamento a `127.0.0.1`, a ausência de telemetria e de verificação de atualização e o modo de observação do tráfego estão especificados? [Mensurabilidade, Spec §FR-037, §SC-004, §US7-1/2] {auto}
- [x] CHK006 - Fica definido que downloads só ocorrem após confirmação explícita, feitos pelo runtime Docker e por processo de conversão (nunca por conexão do app)? [Clareza, Spec §FR-031, §FR-035, §FR-037; Plan M16] {auto}
- [x] CHK007 - O painel de gestão do RabbitMQ está fora da publicação padrão, com o opt-in descrito e o risco de DNS rebinding tratado? [Completude, Spec §FR-029, "Delta Requirements"; Plan "Painel de gestão do RabbitMQ fora da publicação padrão"] {auto}
- [x] CHK008 - O endereço de publicação das portas permanece fixo em loopback e a validação das portas editáveis (1 a 65535) está definida? [Completude, Spec §FR-013, Edge "Porta de serviço inválida"] {auto}

## Execução de processos e confiança na pasta do repositório

- [x] CHK009 - O risco de executar scripts e `uv run` a partir de uma pasta indicada pela pessoa tem requisito de mitigação (seleção só por diálogo nativo do Rust, verificação de dono, tipo e permissões, `uv` offline)? [Cobertura, Spec §FR-016; Plan M1, M3, M4] {auto}
- [x] CHK010 - A passagem de valores a processos sem interpretação por shell e a validação de argumentos vindos de saída externa estão especificadas? [Clareza, Spec §FR-017, §US2-13; Plan M15] {auto}
- [x] CHK011 - O ambiente dos processos filhos (variáveis herdadas, `PATH`, resolução de executáveis) tem requisito de sanitização? [Completude, Plan M5, M6, M7 (a spec não o exige explicitamente; origem: gate owasp-security)] {auto}
- [x] CHK012 - A superfície da webview não é ampliada (CSP e capabilities inalteradas; processos e conexões só no núcleo Rust)? [Consistência, Plan Summary, Constitution Check I; research Decision 10 (linhas 334-335)] {auto}
- [x] CHK013 - A proteção contra sinal enviado a PID/PGID reciclado (M8) é especificável como está, dado que a disponibilidade de `proc_listpids`/`proc_pidpath` no `libc` 0.2.191 e a checagem do dono do socket em escuta (M2) ficaram "a validar na implementação"? [Assumption, Plan M2, M8] {auto} — Resolvido (feature-00c, tarefa FASE 1, 2026-11-27): plan.md, 'Contrato de falha fechada de M8 e resultado aceitável de M2'.

## Arquivos, registros e estado

- [x] CHK014 - Permissões e atomicidade dos arquivos do app (`.env` 0600, `stack-settings.json`/`worker-pool.json` 0600, diretórios 0700, rejeição de link simbólico) estão definidas? [Completude, Spec §FR-010, §FR-012; Plan M9, M14, M17, M18] {auto}
- [x] CHK015 - Registros e buffers têm limite e mascaramento definidos (até 200 linhas do pool, 500 do log técnico, credenciais mascaradas antes de qualquer buffer)? [Mensurabilidade, Spec §FR-023; data-model.md (linhas 122, 187); Plan M10, M20] {auto}
- [x] CHK016 - A pasta de dados tem requisitos contra caminho amplo demais e link simbólico? [Cobertura de edge case, Spec §FR-012, Edge "Pasta de dados inexistente..."; Plan M14] {auto}
- [x] CHK017 - A impossibilidade de apagar dados ou atualizar imagens pelo app está explícita (reduz superfície destrutiva)? [Clareza, Spec §FR-008] {auto}

## Riscos aceitos

- [ ] CHK018 - O risco M19 (credenciais ficam no ambiente do processo `uv`, visível ao mesmo usuário e a root) é aceitável para o público e os dados desta aplicação? Registro no state: aceito pelo operador em block-004; permanece em aberto aqui até confirmação do dono do produto. [Risco, Plan M19, Spec §FR-011] {humano}
- [ ] CHK019 - Os riscos M11 (tags de imagem mutáveis) e M12 (modelo na revisão `main`) permanecem aceitos nesta feature, com a correção adiada para a `container-stack`/fixação futura? Registro no state: aceitos pelo operador em block-004; permanecem em aberto aqui até confirmação. [Risco, Plan M11, M12] {humano}

## Notes

- Items `{auto}` resolvidos pelo agente; CHK013 é `[Assumption]` e vira tarefa de validação no create-tasks
- Items `{humano}` ficam `[ ]` para o dono do produto, mesmo havendo registro de aceite do operador no state (o agente não os auto-marca)
