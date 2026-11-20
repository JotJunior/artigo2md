# Requirements Checklist: Interface web local

**Purpose**: Validar a qualidade dos requisitos da spec reconstituída `web-interface` (completude, clareza, consistência e cobertura), não a implementação.
**Created**: 2026-11-20
**Feature**: [spec.md](../spec.md)

## Completude de Requisitos

- [x] CHK001 - Cada requisito funcional observado cita a origem no código? [Completude, Spec §Functional Requirements] {auto} — FR-001 a FR-013 têm origem citada (fonte: `docs/specs/web-interface/spec.md:36`)
- [ ] CHK002 - Estão definidos limites de tamanho de arquivo e de quantidade de arquivos por envio? [Gap, Spec §FR-003] {auto} — FR-003 não traz limite (fonte: `docs/specs/web-interface/spec.md:38`)
- [ ] CHK003 - Está definida a retenção ou limpeza dos arquivos guardados no diretório de dados? [Gap, Spec §FR-006] {auto} — só há a remoção manual de FR-010 (fonte: `docs/specs/web-interface/spec.md:41`)
- [ ] CHK004 - O controle de acesso à interface está escrito como requisito? [Gap, Spec §INF-001] {auto} — a ausência de autenticação aparece só como inferência (fonte: `docs/specs/web-interface/spec.md:52`)

## Clareza de Requisitos

- [x] CHK005 - As respostas de erro estão especificadas com o código de cada caso? [Clareza, Spec §FR-003, Spec §FR-007, Spec §FR-008, Spec §FR-010] {auto} — 400, 404 e 409 com a condição de cada um (fonte: `docs/specs/web-interface/spec.md:38`)
- [x] CHK006 - O limite do nome do arquivo de saída está quantificado? [Clareza, Spec §FR-013] {auto} — 150 caracteres (fonte: `docs/specs/web-interface/spec.md:48`)
- [ ] CHK007 - O conjunto de caracteres permitido no nome de saída está enumerado? [Ambiguity, Spec §FR-013] {auto} — o requisito diz "conjunto permitido" sem listá-lo (fonte: `docs/specs/web-interface/spec.md:48`)
- [ ] CHK008 - Está definido o que é a "assinatura de PDF" usada para recusar arquivos? [Ambiguity, Spec §FR-004] {auto} — o termo aparece sem definição (fonte: `docs/specs/web-interface/spec.md:39`)
- [ ] CHK009 - Os valores aceitos para o motor no envio estão enumerados? [Ambiguity, Spec §FR-003] {auto} — FR-003 cita só o padrão auto (fonte: `docs/specs/web-interface/spec.md:38`)

## Consistencia de Requisitos

- [x] CHK010 - Os estados que bloqueiam a remoção são os mesmos que viram erro no reinício? [Consistência, Spec §FR-010, Spec §FR-011] {auto} — ambos usam "em fila ou em andamento" (fonte: `docs/specs/web-interface/spec.md:45`)

## Qualidade de Criterios de Aceite

- [ ] CHK011 - Há critério de sucesso mensurável para a capacidade? [Gap, Spec §SC-001] {auto} — SC-001 é um campo pendente (fonte: `docs/specs/web-interface/spec.md:62`)
- [ ] CHK012 - A prioridade da user story reflete o valor da capacidade para o produto? [Prioridade, Spec §User Story 1] {humano}

## Cobertura de Cenarios

- [x] CHK013 - Cada requisito funcional é citado em ao menos um cenário ou edge case? [Cobertura, Spec §Acceptance Scenarios] {auto} — gate requirement-coverage: requirements=13, covered=13, errors=0
- [ ] CHK014 - Cada requisito funcional tem cenário com teste existente como origem? [Gap, Spec §FR-011, Spec §FR-012, Spec §FR-013] {auto} — esses requisitos só aparecem em cenários ou edge cases pendentes (fonte: `docs/specs/web-interface/spec.md:24`)

## Requisitos Nao-Funcionais

- [ ] CHK015 - Está definido um tamanho máximo para a fila de conversão? [Gap, Spec §FR-005] {auto} — FR-005 só define uma conversão por vez (fonte: `docs/specs/web-interface/spec.md:40`)
- [ ] CHK016 - A ausência de autenticação é aceitável para o uso em rede previsto pelo produto? [Risco, Spec §INF-001] {humano}

## Notes

- Items `{auto}` ja vem resolvidos pelo agente (`[x]` com citacao, ou marcador `[Gap]`/`[Ambiguity]`)
- Items `{humano}` ficam `[ ]` aguardando decisao do dono do produto
- Marcar items concluidos com `[x]`
- Items numerados sequencialmente para referencia
