# Performance Checklist: Controle da infraestrutura pelo app desktop

**Purpose**: Validar a qualidade dos requisitos de tempo, capacidade, recursos e responsividade (sondagens, downloads, subida e parada).
**Created**: 2026-11-26
**Feature**: [spec.md](../spec.md)

## Alvos mensuráveis

- [x] CHK001 - Os alvos de tempo de subida (SC-001, 3 min) e de parada (SC-005, 60 s) são mensuráveis e têm linha de base citada? [Mensurabilidade, Spec §SC-001 (base 2 min em container-stack SC-001), §SC-005] {auto}
- [x] CHK002 - O alvo de 10 s para refletir mudança de estado (SC-003) é compatível com a cadência definida? Pior caso pela aritmética dos valores do plano: sondagem a cada 2 s + até 1 s de conexão e 1 s de leitura + leitura do retrato pela interface a cada 3 s, cerca de 7 s. [Consistência, Spec §SC-003, §FR-027; Plan Technical Context, M13; contracts/tauri-commands.md `get_infra_snapshot`] {auto}
- [x] CHK003 - O tempo máximo de cada verificação, citado como "curto" na spec, está quantificado em algum artefato? [Clareza, Spec §FR-027; Plan M13 (1 s/1 s), M20] {auto}
- [x] CHK004 - A cadência de atualização do progresso de download (5 s na spec) é sustentada pela fonte do progresso (no máximo 1 evento por segundo por modelo)? [Consistência, Spec §FR-032, §SC-008; contracts/models-cli.md `progress`] {auto}
- [x] CHK005 - O orçamento de 60 s de SC-005 está decomposto de forma viável? Parada calma do pool até 45 s (30 s de prazo do worker mais margem) somada ao `down.sh`, que não tem prazo declarado; o plano delega a verificação ao teste de integração, mas a spec não diz o que acontece se a soma passar de 60 s (Edge usa 30 s e depois força). [Ambiguity, Spec §SC-005, §FR-020; Plan Technical Context (Performance Goals)] {auto} — Resolvido (feature-00c, tarefa FASE 1, 2026-11-27): spec.md SC-005 (orçamento pela soma das ações, regra de falha) e plan.md Performance Goals.

## Recursos e capacidade

- [x] CHK006 - Os padrões de workers (4) e de memória livre mínima (4822 MB) têm fonte e a medição que os sustenta? [Rastreabilidade, Spec §FR-019; `docs/specs/conversion-queue/ram-measurement.md`] {auto}
- [x] CHK007 - O comportamento sob pouca memória (adiar conversores, avisar, não tratar como falha) e com pool acima da RAM (aviso do worker) está definido? [Cobertura, Spec §FR-019, §FR-023, Edge "Falta de memória livre"] {auto}
- [x] CHK008 - A verificação de espaço em disco antes de download tem requisito e fonte dos tamanhos (≈3 GB de modelos no README; ≈1,5 GB de imagens medido), inclusive para o disco virtual do runtime, cuja leitura foi declarada inviável? [Cobertura, Spec §FR-031, §FR-033; Plan "Riscos e pontos a validar"; contracts/models-cli.md evento `disk`] {auto}
- [x] CHK009 - O requisito de não deixar processos nem containers residuais após parar está medido e tem prazo? [Mensurabilidade, Spec §SC-005, §FR-020] {auto}

## Responsividade e concorrência

- [x] CHK010 - Há requisito de que operações longas e verificações lentas nunca congelem a janela, com tempo máximo e uma verificação por vez? [Completude, Spec §FR-015, §FR-027, Edge "Verificação de saúde lenta"; Plan M20] {auto}
- [x] CHK011 - Uma só operação de ciclo de vida por vez está especificada, inclusive entre instâncias? [Clareza, Spec §FR-015; Plan M17 (`lifecycle.lock`)] {auto}
- [x] CHK012 - A política de repetição do pool evita laço de reinício e custo descontrolado? [Cobertura, Spec §FR-022, §US3-8] {auto}
- [x] CHK013 - A cadência das sondagens quando a janela não está visível é consistente? A spec diz "enquanto o painel estiver visível" (FR-027), mas o indicador resumido é visível em toda tela e o plano mantém um temporizador do núcleo a cada 2 s sem condição de visibilidade. [Ambiguity, Spec §FR-025, §FR-027; contracts/tauri-commands.md `get_infra_snapshot`] {auto} — Resolvido (feature-00c, tarefa FASE 1, 2026-11-27): spec.md FR-027 (sondagem a cada 2 s com o app aberto; cadência com janela oculta mantida; CHK016 segue pendente).

## Decisões de produto

- [ ] CHK014 - As metas de 3 min (SC-001) e de 60 s (SC-005) correspondem ao que o dono do produto considera aceitável para a pessoa não técnica? [Risco, Spec §SC-001, §SC-005] {humano}
- [ ] CHK015 - Os padrões de 4 workers e 4822 MB de memória livre mínima são aceitáveis para os Macs com pouca RAM do público-alvo, dado que só há aviso (sem limitação automática)? [Risco, Spec §FR-019] {humano}
- [ ] CHK016 - A frequência de sondagem contínua em segundo plano (custo de bateria) versus a frescura do indicador está de acordo com o apetite do produto? [Risco, Spec §FR-027] {humano}

## Notes

- Items `{auto}` resolvidos pelo agente; CHK005 e CHK013 vão para `/clarify` ou tarefa de definição no create-tasks
- Items `{humano}` ficam `[ ]` para o dono do produto
