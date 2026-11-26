# Security Checklist: Esqueleto do app desktop macOS

**Purpose**: Validar a qualidade dos requisitos de segurança (rede, confinamento de caminhos, conteúdo não confiável, configurações, cadeia de dependências) antes do create-tasks.
**Created**: 2026-11-24
**Feature**: [spec.md](../spec.md)

## Isolamento de rede

- [x] CHK001 - Os requisitos de "nenhuma conexão de rede" cobrem todos os vetores de carga (imagens, fontes, scripts, links)? [Completude, Spec §FR-015, §FR-016, §FR-040] {auto}
- [x] CHK002 - O critério de "zero conexões" é verificável por teste automatizado, e não só por observação manual? [Mensurabilidade, Spec §SC-004; plan.md §Endurecimento de segurança, teste (a) CSP e (b) HTML sem `src`/`href` remotos] {auto}
- [x] CHK003 - A CSP tem diretivas explícitas, sem depender de padrões implícitos? [Clareza, plan.md §Endurecimento de segurança, "Navegação": `base-uri 'none'; form-action 'none'; object-src 'none'; frame-src 'none'`] {auto}
- [ ] CHK004 - O requisito "a janela só navega para a origem do próprio app e não abre novas janelas" tem mecanismo definido? [Ambiguity, plan.md §Endurecimento de segurança: "mecanismo do Tauri a verificar"] {auto}

## Confinamento de caminhos e fronteira de confiança

- [x] CHK005 - A fronteira de confiança entre webview e lado nativo está definida (nenhum caminho absoluto vem da interface)? [Completude, plan.md §Fronteira de confiança; contracts/commands.md §convenções; H1 aprovado no block-006] {auto}
- [x] CHK006 - O confinamento à pasta aberta está definido igualmente para imagens e para links? [Consistência, Spec §FR-032, §FR-040] {auto}
- [x] CHK007 - A ordem de validação (confinamento antes de existência) está definida, para que caminhos inexistentes fora da raiz não vazem informação? [Clareza, plan.md §Endurecimento de segurança, "Confinamento léxico primeiro"; contracts/commands.md §read_image, validação em ordem] {auto}
- [x] CHK008 - Atalhos simbólicos para fora da pasta e ciclos têm requisito explícito? [Cobertura, Spec §FR-030, Edge Cases] {auto}
- [x] CHK009 - Nomes que diferem só por acento ou caixa têm regra de resolução definida? [Edge Case, Spec §FR-013, Edge Cases] {auto}

## Conteúdo não confiável (Markdown)

- [x] CHK010 - O tratamento de HTML bruto no Markdown está definido como não interpretado? [Completude, Spec §FR-017] {auto}
- [x] CHK011 - A sanitização tem lista explícita de tags e atributos proibidos e comportamento de falha fechada? [Clareza, plan.md §Endurecimento de segurança, "Sanitização no thread principal"] {auto}
- [x] CHK012 - A validação de formato de imagem exige extensão e assinatura do arquivo, não só extensão? [Completude, Spec §FR-041; contracts/commands.md §ErrorCode `unsupportedFormat`] {auto}
- [x] CHK013 - Os comandos LaTeX capazes de gerar link ou carregar recurso (`\href`, `\url`, `\includegraphics`) estão definidos como inertes? [Completude, plan.md §Endurecimento de segurança, "KaTeX"] {auto}
- [ ] CHK014 - Os limites de expansão do KaTeX (proteção contra fórmula que consome CPU/memória) estão quantificados? [Gap, plan.md §Endurecimento de segurança: "Limites de expansão do KaTeX fixados no execute-task"] {auto}

## Limites de recursos

- [x] CHK015 - Os limites rígidos de leitura estão quantificados por tipo de arquivo? [Clareza, plan.md §Endurecimento de segurança: `.md` 50.000.000 bytes, imagem 20.000.000 bytes; contracts/commands.md §ErrorCode `tooLarge`] {auto}
- [ ] CHK016 - A recusa de arquivo acima do limite rígido tem requisito na spec, com texto literal ao usuário? [Gap, Spec §FR-034 cobre só o aviso de arquivo grande; contracts/commands.md §ErrorCode `tooLarge`: "mensagem em português (definida no execute-task)"] {auto}
- [ ] CHK017 - Está definido o comportamento quando o arquivo cresce entre a conferência de metadados e a leitura (leitura limitada ao teto, não só checagem prévia)? [Gap, plan.md §Endurecimento de segurança: "conferem o tamanho pelos metadados antes de ler"] {auto}
- [x] CHK018 - A concorrência de pedidos de imagem e o descarte ao trocar de documento estão definidos? [Completude, plan.md §Endurecimento de segurança: fila com concorrência limitada, Worker encerrado, `blob:` revogados] {auto}

## Configurações e erros

- [x] CHK019 - As configurações recebidas da interface têm esquema e domínio de valores definidos, com erro para valor fora do esquema? [Completude, data-model.md §AppSettings; contracts/commands.md §ErrorCode `invalidSettings`] {auto}
- [x] CHK020 - As gravações concorrentes do `settings.json` têm requisito de serialização? [Cobertura, contracts/commands.md §convenções: mesmo `Mutex` no Rust] {auto}
- [x] CHK021 - As respostas de erro à interface estão definidas sem texto técnico (sem caminho absoluto nem mensagem do sistema)? [Clareza, contracts/commands.md §Error, `detail` sempre `null`] {auto}
- [ ] CHK022 - O destino, o formato e a retenção do log do lado Rust (que pode conter caminhos absolutos) estão definidos? [Gap, contracts/commands.md §Error: "registrado só no log do lado Rust", sem local nem retenção] {auto}
- [ ] CHK023 - As permissões (capabilities) do webview estão listadas uma a uma? [Gap, contracts/commands.md §Capabilities: "listadas uma a uma no execute-task"] {auto}

## Cadeia de dependências e distribuição

- [x] CHK024 - Há requisito de licença permissiva verificada em fonte oficial para toda dependência obrigatória? [Completude, Spec §FR-037; plan.md tabela de licenças] {auto}
- [x] CHK025 - A checagem de vulnerabilidades de dependências npm e cargo está definida antes do commit? [Completude, plan.md §Endurecimento de segurança, "Cadeia de dependências": `npm audit` e `cargo audit` (aprovado no block-006)] {auto}
- [x] CHK026 - O risco da distribuição sem assinatura está documentado como aceito, com mitigação? [Risco, Spec §FR-027; plan.md §Endurecimento de segurança: SHA-256 publicado no README de `desktop/`] {auto}
- [ ] CHK027 - A ausência de modo alternativo para `.md` acima de 50 MB (recusa simples, sem exibição em texto puro) atende ao público-alvo? [Risco, decisão do operador no block-006] {humano}

## Notes

- Items `{auto}` vêm resolvidos pelo agente (`[x]` com citação, ou marcador `[Gap]`/`[Ambiguity]`).
- Items `{humano}` ficam `[ ]` aguardando decisão do dono do produto.
- Destinos: `[Gap]` (CHK014, CHK016, CHK017, CHK022, CHK023) viram tarefas no create-tasks; `[Ambiguity]` CHK004 é resolvido no execute-task pela verificação da API do Tauri (research "A verificar").
- Gate `requirement-coverage.sh` sobre spec.md: 42 requisitos, 42 cobertos, 0 erros.
