# Implementation Plan: Esqueleto do app desktop macOS

**Feature**: `desktop-shell` | **Date**: 2026-11-24 | **Spec**: [spec.md](./spec.md)

## Summary

Criar em `desktop/` um app Tauri 2 para macOS (Apple Silicon, macOS 11.0 ou mais novo) com janela de barra lateral, árvore somente leitura da pasta escolhida, visualizador de Markdown (GFM, KaTeX, imagens relativas) e configurações persistidas. O lado Rust concentra todo acesso ao disco e todo confinamento de caminho; a interface Svelte 5 + TypeScript só recebe dados já validados. Nenhuma conexão de rede: CSP restrita a `'self'` e `blob:`, nenhum recurso remoto, HTML bruto desligado. O build gera um `.app` sem assinatura por um comando documentado. A CLI, a interface web e a suíte Python não mudam.

Decisões e fontes: [research.md](./research.md) (R1 a R8, Decisions 1 a 9).

## Technical Context

**Language/Version**: Rust 1.90 ou mais novo (MSRV do `tauri` 2.12.7, R3); TypeScript 7.0.2 (R4)
**Primary Dependencies**: Tauri 2 (`tauri` 2.12.7, `tauri-plugin-dialog` 2.8.4), Svelte 5 (5.59.1) + Vite 8.3.8, markdown-it 15.0.3, katex 0.19.1 + @vscode/markdown-it-katex 1.1.2, dompurify 3.5.0 (lista completa abaixo)
**Storage**: um arquivo `settings.json` no diretório de configuração do app (API a confirmar, research "A verificar"); nada além disso
**Testing**: `cargo test` (com `tempfile` 3.27.0) e `vitest` 5.1.1 com `jsdom` 31.0.1; `uv run pytest` continua passando sem alteração
**Target Platform**: macOS 11.0 ou mais novo, somente Apple Silicon (arm64) (block-001, block-004; dec-029)
**Project Type**: app desktop (Tauri 2: núcleo Rust + webview)
**Performance Goals**: SC-002 (5.000 itens, primeiro nível em até 2 s, janela respondendo); FR-029 (arquivos grandes sem congelar)
**Constraints**: zero rede (FR-015, Princípio I); somente leitura no disco do usuário (FR-006); textos de UI em português (Princípio III); dependências com licença permissiva, sem GPL/AGPL (Princípio II, constitution 1.2.0)
**Scale/Scope**: uma pasta aberta por vez; quatro telas lógicas (Arquivos vazia, Arquivos com árvore e visualizador, Configurações, avisos)

### Pré-requisitos de ambiente

- Rust/cargo 1.90 ou mais novo: instalado pelo operador via rustup (onda-009: `cargo --version` → 1.100.0, `rustc --version` → 1.100.0). `cargo-audit` ainda não instalado; instalação aprovada no block-006.
- Node.js e npm: necessários para o front e para o `@tauri-apps/cli`. A versão mínima de Node não foi verificada em fonte nesta onda; conferir na doc de pré-requisitos do Tauri no execute-task.

### Dependências e licenças (FR-037)

Fonte das versões e licenças: research R3 (crates.io API) e R4 (`npm view`).

| Lado | Pacote | Versão | Licença | Tipo |
|------|--------|--------|---------|------|
| cargo | tauri | 2.12.7 | Apache-2.0 OR MIT | obrigatória |
| cargo | tauri-build | 2.7.6 | Apache-2.0 OR MIT | build |
| cargo | tauri-plugin-dialog | 2.8.4 | Apache-2.0 OR MIT | obrigatória |
| cargo | serde | 1.0.229 | MIT OR Apache-2.0 | obrigatória |
| cargo | serde_json | 1.0.154 | MIT OR Apache-2.0 | obrigatória |
| cargo | thiserror | 2.0.21 | MIT OR Apache-2.0 | obrigatória |
| cargo | tempfile | 3.27.0 | MIT OR Apache-2.0 | dev |
| npm | @tauri-apps/api | 2.12.5 | Apache-2.0 OR MIT | obrigatória |
| npm | @tauri-apps/plugin-dialog | 2.8.4 | MIT OR Apache-2.0 | removida (tarefa 8.2.1): a interface não o importa; o diálogo de pasta é aberto pelo Rust (`tauri-plugin-dialog`, cargo) |
| npm | markdown-it | 15.0.3 | MIT | obrigatória |
| npm | @vscode/markdown-it-katex | 1.1.2 | MIT | obrigatória |
| npm | katex | 0.19.1 | MIT | obrigatória |
| npm | dompurify | 3.5.0 | (MPL-2.0 OR Apache-2.0), usada sob Apache-2.0 | obrigatória |
| npm | svelte | 5.59.1 | MIT | obrigatória |
| npm | @tauri-apps/cli | 2.12.6 | Apache-2.0 OR MIT | dev |
| npm | @sveltejs/vite-plugin-svelte | 7.3.2 | MIT | dev |
| npm | vite | 8.3.8 | MIT | dev |
| npm | typescript | 7.0.2 | Apache-2.0 | dev |
| npm | vitest | 5.1.1 | MIT | dev |
| npm | jsdom | 31.0.1 | MIT | dev |
| npm | @types/markdown-it | 14.2.0 | MIT | dev |

Descartadas: `tauri-plugin-store` (Decision 4), `unicode-normalization` (R3), `markdown-it-texmath` e `happy-dom` (alternativas não usadas). As dependências transitivas são conferidas no execute-task por relatório de licenças dos lockfiles (`Cargo.lock`, `package-lock.json`) antes do primeiro commit em `desktop/`; qualquer GPL/AGPL obrigatória bloqueia o commit.

#### Licenças transitivas registradas (Princípio II, constitution 1.2.0)

Fonte: `cargo metadata --format-version 1 --filter-platform aarch64-apple-darwin --locked` sobre `desktop/src-tauri/Cargo.lock` e `package-lock.json` (campo `license` de cada pacote), conferido no execute-task (research R9.6 e R9.8). Nenhuma GPL/AGPL no grafo macOS arm64 nem nas dependências npm. A emenda 1.2.0 (aceita pelo operador no block-008) permite licença permissiva sem copyleft e, em dependência transitiva, copyleft fraco no nível de arquivo (MPL-2.0) quando exigida por dependência direta permissiva, sem modificação e com nome e licença registrados aqui.

| Licença | Crate/pacote | Versão | Exigido por | Modificado? |
|---------|--------------|--------|-------------|-------------|
| MPL-2.0 | cssparser | 0.37.0 | `dom_query` → `tauri-utils` → `tauri` | não |
| MPL-2.0 | cssparser-macros | 0.7.1 | `cssparser` | não |
| MPL-2.0 | dtoa-short | 0.3.5 | `cssparser` | não |
| MPL-2.0 | selectors | 0.38.0 | `dom_query` → `tauri-utils` → `tauri` | não |
| MPL-2.0 | option-ext | 0.2.0 | `dirs-sys` → `dirs` → `tauri`, `wry` | não |
| Unicode-3.0 | icu_collections, icu_locale_core, icu_normalizer, icu_normalizer_data, icu_properties, icu_properties_data, icu_provider | 2.4.0 | `idna_adapter` → `idna` → `url` → `tauri-utils` | não |
| Unicode-3.0 | litemap | 0.8.3 | `icu_*` | não |
| Unicode-3.0 | potential_utf | 0.1.6 | `icu_*` | não |
| Unicode-3.0 | tinystr | 0.8.5 | `icu_*` | não |
| Unicode-3.0 | writeable | 0.6.4 | `icu_*` | não |
| Unicode-3.0 | yoke / yoke-derive | 0.8.4 | `icu_*` | não |
| Unicode-3.0 | zerofrom / zerofrom-derive | 0.1.8 | `yoke` | não |
| Unicode-3.0 | zerotrie | 0.2.5 | `icu_*` | não |
| Unicode-3.0 | zerovec / zerovec-derive | 0.11.9 / 0.11.7 | `icu_*` | não |
| (MIT OR Apache-2.0) AND Unicode-3.0 | unicode-ident | 1.0.27 | `proc-macro2`, `syn` | não |
| PSF-2.0 (npm, permissiva) | argparse | 3.0.4 | `markdown-it` | não |
| MPL-2.0 (npm, só desenvolvimento) | lightningcss e binários por plataforma | 1.33.0 | `vite` (ferramenta de build; não entra no `.app`) | não |

Total: 5 crates MPL-2.0 e 18 crates Unicode-3.0 no grafo macOS arm64 (mais `unicode-ident`). Nenhum deles é dependência direta do projeto; nenhum foi bifurcado nem editado. Outras licenças permissivas aparecem só como valor do campo `license` nos lockfiles (ISC, Zlib, 0BSD, BSD-2/3-Clause, BlueOak-1.0.0, CC0-1.0 e MIT-0 em opção dupla): todas sem copyleft.

## Constitution Check

*GATE: deve passar antes do Phase 0. Re-checado após o Phase 1 (abaixo).*

| Princípio | Status | Notas |
|-----------|--------|-------|
| I. Processamento 100% Local | PASS | Sem backend, sem serviço e sem porta: o app não escuta em nenhuma porta nesta feature. CSP sem origem remota; `linkify: false`; links externos sem `href`; imagens remotas nunca carregadas (FR-015, FR-016). Fontes do KaTeX servidas localmente (`'self'`), nunca de CDN; como empacotá-las a partir do pacote `katex` fica a verificar (research "A verificar"). Nenhuma telemetria. |
| II. Licenças permissivas | PASS | Todas as dependências diretas são MIT/Apache (tabela acima); DOMPurify usada sob Apache-2.0. Transitivas conferidas no execute-task (research R9.6 e R9.8): sem GPL/AGPL; 5 crates MPL-2.0 e 18 Unicode-3.0, transitivos do Tauri e sem modificação, registrados por nome e licença na seção acima conforme a constitution 1.2.0. |
| III. Português | PASS | Catálogo único de mensagens em `desktop/src/lib/messages.ts`; identificadores, comandos e campos JSON em inglês (regra global do operador). |
| IV. Testes antes do commit | PASS | `cargo test` e `npm test` (vitest) cobrem FR-038; `uv run pytest` segue intacto (`testpaths = ["tests"]` em `pyproject.toml`, e o wheel empacota só `src/artigo2md`). |
| V. Veracidade de dados | PASS | Versões, licenças e APIs citadas com fonte (research R1 a R8); itens sem fonte ficam em "A verificar no execute-task", não no desenho como fato. Contratos de comando são PROPOSTA nova (não descrevem API existente). |

## Arquitetura

```
┌──────────────────── webview (Svelte 5 + TS) ────────────────────┐
│ App.svelte ─ Sidebar ─ FilesView (FileTree + Viewer) ─ Settings  │
│        │ invoke() (@tauri-apps/api/core)        ▲ Blob URLs      │
│        ▼                                         │               │
│ lib/api.ts (único ponto de invoke)   render.worker.ts            │
│                                      (markdown-it + KaTeX)       │
│                                      → DOMPurify → DOM           │
└────────────────────────────┬────────────────────────────────────┘
                             │ IPC (comandos Tauri)
┌────────────────────────────▼──────────── núcleo Rust ───────────┐
│ commands.rs → fs_tree.rs | document.rs | image.rs | settings.rs │
│                 └──────── paths.rs (confinamento) ────────┘      │
│ AppState { root: Mutex<Option<PathBuf>> }                        │
└──────────────────────────────────────────────────────────────────┘
```

- **Fronteira de confiança**: o conteúdo do `.md` é não confiável, e por isso o próprio webview também é tratado como não confiável. Nenhum caminho absoluto vai ao webview nem vem dele: a raiz é escolhida pelo diálogo aberto no Rust e `lastFolder` só é escrito pelo Rust. Todo acesso passa por `paths.rs` (confinamento léxico antes de tocar o disco, depois canonização e conferência por componente; Decisions 5 e 6, contracts/commands.md).
- **Responsividade (FR-029)**: comandos de disco são `async` (rodam fora da thread principal, R6); listagem é por nível, sob demanda; renderização em Web Worker; o aviso de arquivo grande aparece antes do resultado da renderização.
- **Somente leitura (FR-006)**: o lado Rust não expõe nenhum comando que escreva fora do `settings.json`. Capabilities só com o necessário: nenhuma permissão de diálogo nem de arquivos para o webview; permissões do core listadas uma a uma no execute-task (R6), sem conjunto padrão inteiro.
- **Rede (FR-015)**: CSP proposta `default-src 'self'; img-src 'self' blob:; font-src 'self'; style-src 'self'; script-src 'self'; worker-src 'self'; connect-src ipc: http://ipc.localhost`. O valor de `connect-src` vem do exemplo oficial (R6); a chave exata e a necessidade de `'unsafe-inline'` em `style-src` para o KaTeX ficam a verificar (research).
- **Renderização (Decision 8)**:
  - `html: false` (FR-017).
  - KaTeX com `throwOnError: false`: fórmula inválida mostra o texto original na cor de erro (FR-033).
  - Regras do renderer do markdown-it trocadas (nomes das regras e mecanismo a confirmar na doc do markdown-it, research "A verificar"):
    - `image`: cria um marcador com o `src` original, depois resolvido por `read_image` (FR-013, FR-014, FR-016, FR-032, FR-041).
    - `link_open`/`link_close`: link relativo para `.md` vira um elemento com `data-md-link`; os demais ficam inativos (FR-040, Decision 9).
- **Configurações (Decision 4)**: `settings.json` validado por esquema no Rust; inválido devolve padrões e `warning = "settingsReset"` (FR-023); escrita atômica.

## Convenções de Borda

| Camada | Case style | Validação | Fonte da verdade |
|--------|------------|-----------|------------------|
| Structs Rust | snake_case nos campos, renomeados para camelCase na serialização pelo serde (atributo a confirmar, research "A verificar") | serde + validação de esquema em `settings.rs` | `desktop/src-tauri/src/model.rs` |
| Payload IPC (args e retornos) | camelCase | serde no Rust; tipos TS espelhados | [contracts/commands.md](./contracts/commands.md) |
| Nomes de comando | snake_case | `generate_handler!` | `desktop/src-tauri/src/commands.rs` |
| Códigos de erro | camelCase (string) | enum `ErrorCode` serializado | contracts/commands.md §Erros |
| Tipos TS | camelCase | tipos em `desktop/src/lib/types.ts`, testes de contrato | contracts/commands.md |
| `settings.json` | camelCase | esquema em `settings.rs` | [data-model.md](./data-model.md) |
| Textos de UI | português | catálogo `messages.ts` | spec.md (textos literais dos FRs) |

**Mapper layer**: não há banco; o único mapeamento é o `serde` (snake_case ↔ camelCase) em `model.rs`. O shape de cada comando é conferido por um teste de contrato que compara a serialização real do Rust (JSON gerado em `cargo test`) com os tipos de `types.ts` (cenário 9 do quickstart).

## Project Structure

### Documentation (this feature)

```
docs/specs/desktop-shell/
├── spec.md
├── plan.md            # este arquivo
├── research.md        # Phase 0
├── data-model.md      # Phase 1
├── quickstart.md      # Phase 1
└── contracts/
    └── commands.md    # Phase 1 (comandos Tauri invoke)
```

### Source Code (repository root)

Existente e **não alterado** (FR-036): `src/artigo2md/`, `tests/`, `pyproject.toml`, `uv.lock`, `README.md`. Alteração mínima: `.gitignore` ganha `desktop/node_modules/`, `desktop/src-tauri/target/` e a pasta de saída do Vite (proposta `desktop/dist/`, a confirmar no template).

Novo (estrutura do lado Rust segundo https://v2.tauri.app/start/project-structure/):

```
desktop/
├── package.json             # scripts: dev, build, test, tauri
├── package-lock.json
├── tsconfig.json
├── vite.config.ts
├── index.html
├── README.md                # pré-requisitos, comando único de build, Gatekeeper, SHA-256, licenças
├── src/
│   ├── main.ts
│   ├── env.d.ts             # declaração de módulo *.svelte para o tsc (acrescentado no execute-task)
│   ├── App.svelte
│   ├── components/
│   │   ├── Sidebar.svelte
│   │   ├── FilesView.svelte
│   │   ├── FileTree.svelte
│   │   ├── TreeNode.svelte
│   │   ├── Viewer.svelte
│   │   └── SettingsView.svelte
│   ├── lib/
│   │   ├── api.ts           # único ponto de invoke()
│   │   ├── types.ts         # espelho dos contratos
│   │   ├── messages.ts      # textos pt-BR
│   │   ├── settings.svelte.ts  # estado reativo das configurações
│   │   ├── markdown.ts      # configuração markdown-it + KaTeX + regras image/link (só no Worker)
│   │   ├── refs.ts          # classificação de src/href (sem dependências; usada no thread principal)
│   │   ├── sanitize.ts      # DOMPurify + conversão para fragmento (CSSOM no lugar de style inline)
│   │   ├── render.worker.ts
│   │   ├── render.ts        # sessão de renderização: um Worker por vez, cancelável
│   │   ├── images.ts        # fila de imagens com concorrência limitada e revogação de blob:
│   │   ├── tree.svelte.ts   # modelo reativo da árvore (carregamento sob demanda)
│   │   └── tree-nav.ts      # navegação por teclado (lógica pura)
│   └── styles/
│       └── app.css          # temas claro/escuro, tamanhos de texto
├── tests/                   # vitest
│   ├── markdown.test.ts     # tabelas, fórmulas, HTML bruto, links, imagens
│   ├── sanitize.test.ts
│   ├── messages.test.ts
│   ├── contract.test.ts
│   ├── render.test.ts, images.test.ts, tree-nav.test.ts, tree-model.test.ts, settings.test.ts
│   ├── components.test.ts   # componentes Svelte montados em jsdom
│   └── security-config.test.ts  # CSP sem origem remota, capabilities, minimumSystemVersion
└── src-tauri/
    ├── Cargo.toml
    ├── Cargo.lock
    ├── build.rs
    ├── tauri.conf.json      # minimumSystemVersion 11.0, CSP
    ├── capabilities/
    │   └── default.json
    ├── icons/
    └── src/
        ├── main.rs
        ├── lib.rs           # Builder, plugin dialog, invoke_handler
        ├── commands.rs
        ├── model.rs
        ├── error.rs
        ├── paths.rs         # canonização e confinamento
        ├── fs_tree.rs
        ├── document.rs
        ├── image.rs         # extensão + assinatura
        ├── link.rs          # resolve_link (acrescentado no execute-task)
        ├── state.rs         # AppState (raiz, SettingsStore, Logger)
        ├── limits.rs        # tetos de leitura e limite do aviso de arquivo grande
        ├── log.rs           # log local com rotação (2.3.3)
        └── settings.rs
```

**Structure Decision**: tudo do app em `desktop/` (FR-036), separado do pacote Python. A lógica testável fica em módulos Rust puros (`paths`, `fs_tree`, `document`, `image`, `settings`) chamados por `commands.rs`, para que `cargo test` cubra FR-038 sem subir janela. No front, a lógica de renderização fica em `lib/markdown.ts` e `lib/sanitize.ts`, testada com vitest + jsdom.

## Decisões de desenho fixadas no execute-task (onda-010)

Valores de projeto (decisões desta feature) ou fatos com fonte em [research.md](./research.md) R9. Textos de interface estão no catálogo de [data-model.md](./data-model.md).

- **Indicação de carregamento (2.1.3)**: a árvore mostra o texto `loadingTree` sob o nó enquanto `list_dir` não responde, e o nó recebe `aria-busy="true"`; o visualizador mostra `loadingDocument` entre o clique e o fim da renderização; cada imagem mostra `loadingImage` enquanto o status é `loading`. Sem barra de progresso nem animação obrigatória; o aviso `largeFile` aparece antes do resultado da renderização (FR-034).
- **Link externo inativo (2.2.2, FR-040)**: o link externo é um `<span class="link-inactive">` sem `href` (Decision 9), com sublinhado tracejado, cor atenuada, cursor padrão (sem mão) e `title` = `externalLinkInactive`. Clicar não abre nada nem muda a rota; não há toast nem modal.
- **KaTeX (2.3.1, R9.4)**: `throwOnError: false`, `trust: false`, `maxExpand: 1000` (padrão documentado, fixado explicitamente) e `maxSize: 100` (valor de projeto). Fonte: https://katex.org/docs/options.
- **Leitura limitada ao teto (2.3.2, TOCTOU)**: além de conferir `metadata.len()`, `read_markdown` e `read_image` leem com `File::open` + `Read::take(limite + 1)` e recusam com `tooLarge` se o conteúdo lido exceder o limite (`.md`: 50.000.000 bytes; imagem: 20.000.000 bytes). Assim um arquivo que cresce entre os metadados e a leitura nunca é lido por inteiro na memória além do teto + 1 byte. O contrato registra a mesma regra.
- **Log do lado Rust (2.3.3, CHK022 de segurança)**: o texto técnico dos erros (que pode ter caminho absoluto) vai só para um arquivo local `artigo2md.log` em `app_log_dir()` (macOS: `~/Library/Logs/<identifier>/`, R9.3 e doc do crate `tauri` 2.12.7), uma linha por evento: `<segundos desde a época Unix> <código do erro> <detalhe técnico>`. Permissão do arquivo 0600. Retenção: quando passar de 1.000.000 bytes, o arquivo é renomeado para `artigo2md.log.1` (substituindo o anterior) e um novo é aberto; no máximo 2 arquivos (~2 MB). Nada é enviado para fora da máquina; falha ao gravar o log é ignorada (nunca derruba o comando) e nunca vai para a interface.
- **Navegação e novas janelas (2.3.4, R9.3)**: a janela principal é criada no `setup` (`"create": false` na config + `WebviewWindowBuilder::from_config`) com `on_navigation` permitindo só a origem do próprio app (`tauri://localhost` em produção; `http://localhost:1420` só em desenvolvimento, via `cfg!(dev)`) e `on_new_window` devolvendo `NewWindowResponse::Deny`. A origem exata de produção no macOS é confirmada no cenário 8 do quickstart.
- **Acessibilidade (2.2.3, CHK021/CHK022 de UX, decisão do operador)**: navegação por teclado (barra lateral, árvore e configurações) entra nesta feature, com rótulos `aria-*` nos controles; suporte completo a VoiceOver fica para feature futura. Sub-tarefa 4.3.5 criada na FASE 4.
- **`.md` acima de 50 MB (2.3.5, CHK027 de segurança, decisão do operador)**: continua recusado com `tooLarge` e a mensagem `fileTooLarge`, sem modo alternativo.

## Mapa FR → componente

| FR | Componente |
|----|-----------|
| FR-001, FR-002, FR-039 | `App.svelte`, `Sidebar.svelte`, `settings.svelte.ts` |
| FR-003 | `messages.ts` (todos os textos) |
| FR-004, FR-007 | `FilesView.svelte` + `open_folder` (diálogo aberto pelo Rust com o plugin dialog) + `get_startup_state` |
| FR-005, FR-008, FR-028, FR-029, FR-030 | `fs_tree.rs` (`list_dir`), `FileTree.svelte`, `TreeNode.svelte` |
| FR-006 | ausência de comandos de escrita; capabilities mínimas |
| FR-009, FR-018, FR-031, FR-034, FR-035 | `document.rs` (`read_markdown`), `Viewer.svelte` |
| FR-010, FR-011, FR-012, FR-017, FR-033 | `markdown.ts`, `render.worker.ts`, `sanitize.ts` |
| FR-013, FR-014, FR-016, FR-032, FR-041 | `image.rs` (`read_image`), regra `image` em `markdown.ts` |
| FR-015 | CSP, `linkify: false`, sem recurso remoto |
| FR-019 a FR-024 | `settings.rs`, `SettingsView.svelte` |
| FR-025 a FR-027, FR-042 | `tauri.conf.json`, script `tauri` do `package.json`, quickstart |
| FR-036 | estrutura `desktop/` |
| FR-037 | tabela de licenças acima + relatório de transitivas |
| FR-038 | `cargo test`, `npm test` |
| FR-040 | `resolve_link`, regra `link_open` em `markdown.ts` |

## Endurecimento de segurança (gates owasp-security e validate-documentation, onda-006)

Itens que o desenho passa a exigir. Nomes de API e chaves de configuração marcados "a verificar" estão em research "A verificar no execute-task".

- **Raiz escolhida só pelo Rust (H1)**: `open_folder` sem argumentos; `save_settings` recebe `UserSettings` sem `lastFolder`. Teste `cargo test`: `save_settings` não altera `lastFolder`.
- **Confinamento léxico primeiro**: destino inexistente fora da raiz devolve `outsideFolder`, não `notFound` (FR-032, FR-040). Testes para `../../x.md` e `../../x.png` inexistentes.
- **Sanitização no thread principal**: o Worker só produz HTML (markdown-it + KaTeX); o DOMPurify roda no thread principal, onde há DOM, com lista explícita de tags e atributos permitidos. Proibidos `href`, `src`, `srcset`, `xlink:href`, `action`, `formaction`, e as tags `form`, `iframe`, `object`, `embed`, `use`. Se o DOMPurify não puder sanitizar no ambiente, o documento é mostrado como texto puro (falha fechada). O custo dessa etapa conta no FR-029 e é medido no teste de arquivo grande.
- **Regras próprias do renderer**: o marcador de imagem não é `<img src>` antes da resolução (só atributo `data-*`); atributos vindos do Markdown são escapados pelo utilitário de escape do markdown-it.
- **KaTeX**: `trust: false` explícito (o padrão já é `false`, R7), com teste de que `\href`, `\url` e `\includegraphics` ficam inertes. Limites de expansão do KaTeX fixados no execute-task.
- **Navegação**: a janela só navega para a origem do próprio app e não abre novas janelas (mecanismo do Tauri a verificar). A CSP proposta ganha `base-uri 'none'; form-action 'none'; object-src 'none'; frame-src 'none'`.
- **Limites rígidos de leitura**: `read_markdown` e `read_image` conferem o tamanho pelos metadados antes de ler e recusam acima do limite com `tooLarge`. Limites (decisão do operador no block-006, onda-007, sem fonte externa; MB decimal, mesma convenção da Decision 3): `.md` até 50.000.000 bytes (50 MB) e imagem até 20.000.000 bytes (20 MB); acima disso, `tooLarge`. O limite do aviso de arquivo grande continua 5.000.000 bytes (Decision 3). As imagens de um documento são pedidas por uma fila com concorrência limitada; trocar de documento encerra o Worker em curso e revoga os `blob:`.
- **Teste automático de "nenhuma conexão" (SC-004)**: (a) teste que lê a CSP de `tauri.conf.json` e falha se houver origem remota; (b) teste de renderização que falha se algum `src`/`href` do HTML final apontar para `http:`/`https:`/`//`.
- **Cadeia de dependências**: além do relatório de licenças, `npm ci` com lockfile e checagem de vulnerabilidades (`npm audit`, e `cargo audit`, cuja instalação o operador aprovou no block-006) antes do commit em `desktop/`.
- **Distribuição sem assinatura (risco aceito, FR-027)**: o README de `desktop/` publica o SHA-256 do `.app` gerado.

## Build do `.app` (FR-025, FR-026)

- Comando documentado (R5): `npm run tauri build -- --bundles app`, executado em `desktop/`.
- Arquitetura: somente arm64. A forma de fixar o alvo (`--target aarch64-apple-darwin`, R6) e a pasta de saída do `.app` são confirmadas no primeiro build do execute-task e então escritas no README de `desktop/` e no quickstart.
- Sem identidade de desenvolvedor e sem notarização. O bundle leva assinatura **ad hoc** (`bundle.macOS.signingIdentity` = `"-"`, tarefa 10.1.1), que sela o `Info.plist` e os recursos; só com a assinatura do linker o macOS recusa o `.app` em quarentena como danificado (block-011, R9.13). Abrir com a liberação manual do Gatekeeper (FR-027); `npm run build:app` verifica a assinatura com `codesign --verify --deep --strict` (tarefa 10.2.1).

## Testes (FR-038)

| Área | Ferramenta | Casos mínimos |
|------|-----------|---------------|
| Listagem da árvore | `cargo test` + `tempfile` | ordem pastas-antes/alfabética sem caixa; ocultos omitidos; sem permissão; atalho para fora e ciclo; 5.000 itens |
| Caminhos de imagem e link | `cargo test` | relativo ok; `..` saindo da raiz; atalho apontando para fora; inexistente; nome com acento e caixa diferentes resolve só o exato; SVG/extensão errada; assinatura incompatível com extensão |
| Documento | `cargo test` | `.md` ok; não UTF-8; acima de 5.000.000 bytes marca `isLarge`; apagado entre leituras; não `.md` |
| Configurações | `cargo test` | padrões; ida e volta; JSON ilegível; valor fora do domínio; restaurar padrões |
| Renderização | `vitest` + `jsdom` | tabela GFM com alinhamento; fórmula em linha e em bloco; fórmula inválida isolada; HTML bruto vira texto; link externo sem `href`; link `.md` com `data-md-link`; imagem remota marcada como bloqueada |
| Textos | `vitest` | todas as chaves do catálogo têm texto em português, sem chave vazia |
| Contrato | `vitest` + JSON gerado pelo `cargo test` | shape dos retornos bate com `types.ts` |
| Segurança | `cargo test` + `vitest` | `save_settings` não altera `lastFolder`; caminhos inexistentes fora da raiz → `outsideFolder`; `tooLarge`; CSP sem origem remota e HTML sem `src`/`href` remotos (SC-004); sanitização com tags proibidas; KaTeX inerte |

## Re-check da Constitution (pós-Phase 1)

| Princípio | Status | Notas |
|-----------|--------|-------|
| I | PASS | O desenho não introduziu servidor, porta nem recurso remoto; o webview não tem acesso direto ao disco. |
| II | PASS | O desenho reduziu dependências (sem plugin-store); nenhuma nova fora da tabela. |
| III | PASS | Todos os textos dos FRs vão para o catálogo; testes conferem o catálogo. |
| IV | PASS | Dois comandos de teste declarados (`cargo test`, `npm test`) mais `uv run pytest`. |
| V | PASS | Itens sem fonte continuam marcados para verificação; nenhum caminho de saída de build, chave de configuração ou assinatura JPEG foi afirmado sem fonte. |

## Complexity Tracking

Sem violações.
