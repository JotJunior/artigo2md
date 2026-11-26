# Research (Phase 0 do plan): desktop-shell

**Feature**: `desktop-shell` | **Data**: 2026-11-24 | **Spec**: [spec.md](./spec.md)
**Estado**: completo. Fatos verificados em fonte oficial nesta data (R1 a R5 na onda-004, R6 a R8 na onda-005); as decisões P1 a P3 foram tomadas pelo operador (block-003 a block-005) e estão na seção "Decisões". O que continua sem fonte está em "A verificar no execute-task".

Regra desta pesquisa (Princípio V): cada versão, licença, limite ou capacidade citada traz a fonte. O que não tem fonte fica em "Decisões pendentes" ou "A verificar na implementação", nunca como fato.

## R1. Versão mínima do macOS (FR-042)

| Fato | Fonte |
|------|-------|
| "By default your Tauri application supports macOS 10.13 and above." | https://v2.tauri.app/distribute/macos-application-bundle/ |
| A chave que controla o piso é `tauri.conf.json > bundle > macOS > minimumSystemVersion`; o exemplo da página usa `"12.0"` (exemplo, não padrão) | https://v2.tauri.app/distribute/macos-application-bundle/ |
| A página de pré-requisitos lista "macOS Catalina (10.15) and later" para o ambiente de desenvolvimento | https://v2.tauri.app/start/prerequisites/ |
| "This release of WebKit in Safari 14 adds support for the WebP open-source image format." e "With the release of Safari 14 for macOS Big Sur, ..." | https://webkit.org/blog/11340/new-webkit-features-in-safari-14/ |
| Na mesma página: "These improvements are available to users running ... macOS Big Sur, macOS Catalina and macOS Mojave." | https://webkit.org/blog/11340/new-webkit-features-in-safari-14/ |

Conflito: o piso padrão do Tauri (10.13) é anterior ao WebKit com WebP (Safari 14). Com o piso padrão, o FR-041 (exibir WebP) não fica garantido. A escolha do piso vai ao operador (eixo `ambiente-alvo`), em P2.

## R2. Formatos de imagem do webview (FR-041)

- WebP: suportado a partir do WebKit do Safari 14 (fonte em R1).
- PNG, JPEG e GIF: **a fonte oficial lida não os menciona** (blog do WebKit sobre o Safari 14; a página de notas do Safari 14 da Apple não pôde ser lida porque o conteúdo é montado no navegador). A confirmação fica como item aberto: buscar uma página oficial da Apple ou do WebKit que liste esses formatos antes de fechar o plan. Até lá, o FR-041 continua apoiado só na decisão do operador (dec-015, block-002), e o app filtra por extensão e por assinatura do arquivo, sem depender do que o webview aceitaria.

## R3. Dependências cargo (FR-037)

Consultadas na API do crates.io (`https://crates.io/api/v1/crates/<nome>`), versão estável mais recente:

| Crate | Versão | Licença | MSRV (`rust_version`) | Uso previsto |
|-------|--------|---------|-----------------------|--------------|
| tauri | 2.12.7 | Apache-2.0 OR MIT | 1.90 | núcleo do app |
| tauri-build | 2.7.6 | Apache-2.0 OR MIT | 1.90 | build script |
| tauri-plugin-dialog | 2.8.4 | Apache-2.0 OR MIT | 1.90 | diálogo nativo "Abrir pasta…" (FR-004) |
| tauri-plugin-store | 2.5.1 | Apache-2.0 OR MIT | 1.90 | candidata para configurações (FR-022); alternativa: arquivo JSON próprio com serde_json |
| serde | 1.0.229 | MIT OR Apache-2.0 | 1.56 | serialização |
| serde_json | 1.0.154 | MIT OR Apache-2.0 | 1.71 | configurações |
| thiserror | 2.0.21 | MIT OR Apache-2.0 | 1.77 | erros tipados |
| tempfile (dev) | 3.27.0 | MIT OR Apache-2.0 | 1.63 | pastas temporárias nos testes |
| unicode-normalization | 0.1.26 | (MIT OR Apache-2.0) AND Unicode-3.0 | 1.36 | **descartada**: o FR-013 pede o nome exato do disco, sem normalização (na época a parte Unicode-3.0 também ia contra o Princípio II; desde a constitution 1.2.0 ela é permitida, mas o motivo do FR-013 basta) |

Consequência: o toolchain Rust precisa ser 1.90 ou mais novo (MSRV do `tauri` 2.12.7). Atualização (onda-009): o operador instalou o toolchain via rustup; `cargo --version` e `rustc --version` informam 1.100.0.

## R4. Dependências npm (FR-037)

Consultadas com `npm view <pacote> version license` no registro npm:

| Pacote | Versão | Licença | Uso previsto |
|--------|--------|---------|--------------|
| @tauri-apps/api | 2.12.5 | Apache-2.0 OR MIT | ponte com o lado nativo |
| @tauri-apps/cli | 2.12.6 | Apache-2.0 OR MIT | build e dev |
| @tauri-apps/plugin-dialog | 2.8.4 | MIT OR Apache-2.0 | diálogo nativo |
| @tauri-apps/plugin-store | 2.5.1 | MIT OR Apache-2.0 | candidata (ver R3) |
| markdown-it | 15.0.3 | MIT | renderização Markdown e GFM (FR-010, FR-011) |
| @types/markdown-it | 14.2.0 | MIT | tipos |
| katex | 0.19.1 | MIT | fórmulas LaTeX (FR-012, FR-033) |
| @vscode/markdown-it-katex | 1.1.2 | MIT | candidata para ligar KaTeX ao markdown-it |
| markdown-it-texmath | 1.0.0 | MIT | candidata alternativa |
| dompurify | 3.5.0 | (MPL-2.0 OR Apache-2.0) | só se necessária; uso sob Apache-2.0 |
| vite | 8.3.8 | MIT | bundler |
| vitest | 5.1.1 | MIT | testes npm (FR-038) |
| typescript | 7.0.2 | Apache-2.0 | linguagem da interface |
| jsdom | 31.0.1 | MIT | DOM nos testes |
| happy-dom | 21.0.4 | MIT | alternativa ao jsdom |

Frameworks de interface consultados para a decisão P1 (todos MIT): svelte 5.59.1 (+ @sveltejs/vite-plugin-svelte 7.3.2), react 19.3.1 / react-dom 19.3.1 (+ @vitejs/plugin-react 6.3.0), vue 3.5.46 (+ @vitejs/plugin-vue 6.0.11), solid-js 2.0.3.

As licenças acima são das dependências diretas. As transitivas são conferidas na implementação, com relatório de licenças do lockfile, antes do primeiro commit em `desktop/`.

## R5. Build do `.app` (FR-025, FR-026)

| Fato | Fonte |
|------|-------|
| "To package your app as a macOS application bundle you can use the Tauri CLI and run the `tauri build` command in a Mac computer"; formas listadas incluem `npm run tauri build -- --bundles app` e `cargo tauri build --bundles app` | https://v2.tauri.app/distribute/macos-application-bundle/ |
| Pasta de saída do `.app`: **não informada na página lida** | idem |
| Alvo `aarch64-apple-darwin`: **não citado nas páginas lidas** | https://v2.tauri.app/start/prerequisites/ , página do bundle |

A verificar na implementação: a pasta exata onde o `.app` é gerado e a forma de fixar o alvo arm64. Isso só pode ser observado rodando o build, o que exige o toolchain Rust.

## R6. API do Tauri 2 usada no desenho (verificado em 2026-11-24, onda-005)

| Fato | Fonte |
|------|-------|
| Comando declarado com `#[tauri::command]` e registrado com `.invoke_handler(tauri::generate_handler![...])` no `tauri::Builder::default()`; no JS, `import { invoke } from '@tauri-apps/api/core'` | https://v2.tauri.app/develop/calling-rust/ |
| "Async commands are executed on a separate async task using async_runtime::spawn." Comandos sem `async` rodam na thread principal, salvo `#[tauri::command(async)]` | idem |
| "everything returned from commands must implement serde::Serialize, including errors"; exemplo com `thiserror` e `impl serde::Serialize` manual | idem |
| "To return array buffers in an optimized way, use tauri::ipc::Response" (`tauri::ipc::Response::new(data)`); **o tipo recebido no JS não é informado** | idem |
| CSP: "At compile time, Tauri appends its nonces and hashes to the relevant CSP attributes automatically"; o exemplo usa um objeto `"csp"` com `default-src`, `connect-src` (`ipc: http://ipc.localhost`), `font-src`, `img-src`, `style-src`; **o caminho exato da chave no `tauri.conf.json` não é informado na página** | https://v2.tauri.app/security/csp/ |
| Plugin de diálogo: `.plugin(tauri_plugin_dialog::init())`; JS `import { open } from '@tauri-apps/plugin-dialog'` com a opção `directory` ("whether is a directory selection or not"); permissão `dialog:allow-open` ("Enables the open command without any pre-configured scope"); exige Rust 1.90 ou mais novo | https://v2.tauri.app/plugin/dialog/ |
| "Capability files are either defined as a JSON or a TOML file inside the `src-tauri/capabilities` directory." O significado de `core:default` não é informado nesta página | https://v2.tauri.app/security/capabilities/ |
| Estrutura: "the Rust project is inside `src-tauri/`", com `Cargo.toml`, `build.rs`, `tauri.conf.json`, `src/main.rs`, `src/lib.rs`, `icons/` e `capabilities/default.json` | https://v2.tauri.app/start/project-structure/ |
| `tauri build` aceita `-t, --target <TARGET>` ("Target triple to build against", um dos valores de `rustc --print target-list` ou `universal-apple-darwin`); `aarch64-apple-darwin` aparece só numa nota sobre builds universais. A referência da CLI lista para `--bundles` apenas `deb, rpm, appimage`, em conflito com a página de bundle macOS, que documenta `--bundles app` | https://v2.tauri.app/reference/cli/ , https://v2.tauri.app/distribute/macos-application-bundle/ |
| API do diretório de configuração do app: **não encontrada nas páginas lidas** (só `app_handle.path().app_dir()` num exemplo) | https://v2.tauri.app/develop/calling-rust/ |

## R7. Bibliotecas de renderização (verificado em 2026-11-24, onda-005)

| Fato | Fonte |
|------|-------|
| markdown-it, opção `html` ("Enable HTML tags in source"): padrão `false`; `linkify`: padrão `false` | https://markdown-it.github.io/markdown-it/interfaces/MarkdownItOptions.html |
| markdown-it, `validateLink`: "By default we disable `javascript:`, `vbscript:`, `file:` schemas" | https://markdown-it.github.io/markdown-it/interfaces/MarkdownIt.html |
| markdown-it, tabelas GFM ligadas por padrão: **não informado nas páginas lidas** (confirmado por teste no execute-task) | idem |
| KaTeX `throwOnError`: padrão `true`; com `false`, "render invalid LaTeX as its source code with hover text giving the error", na cor de `errorColor` (padrão `#cc0000`); `trust` padrão `false`; `output` padrão `htmlAndMathml` | https://katex.org/docs/options |
| KaTeX: "Any HTML generated by KaTeX _should_ be safe from `<script>`..." e "it is always a good idea to sanitize the HTML"; nada sobre CSP ou estilos em linha | https://katex.org/docs/security |
| @vscode/markdown-it-katex: `md.use(mk.default, { katex })`; `$...$` em linha, `$$...$$` em bloco; opções do KaTeX passadas no segundo argumento | https://github.com/microsoft/vscode-markdown-it-katex |
| DOMPurify: `DOMPurify.sanitize(dirty, {...})`, opções `ALLOWED_TAGS`, `ALLOWED_ATTR`, `FORBID_TAGS`, `USE_PROFILES`; por padrão permite HTML, SVG e MathML; o repositório indica licenças Apache-2.0 e MPL-2.0 | https://github.com/cure53/DOMPurify |

## R8. Assinaturas dos formatos de imagem (FR-041)

| Formato | Assinatura | Fonte |
|---------|-----------|-------|
| PNG | primeiros 8 bytes `89 50 4E 47 0D 0A 1A 0A` | https://www.w3.org/TR/png-3/ (5.2 PNG signature) |
| GIF | assinatura `GIF` seguida da versão `87a` ou `89a` | https://www.w3.org/Graphics/GIF/spec-gif89a.txt (17. Header) |
| WebP | `RIFF` (4 bytes), tamanho uint32 little-endian (4 bytes), `WEBP` (4 bytes) | https://developers.google.com/speed/webp/docs/riff_container |
| JPEG | **sem fonte lida nesta onda**; a assinatura só entra no código depois de confirmada em fonte oficial no execute-task | - |

## Decisões

### Decision 1: Framework da interface (P1, eixo `stack-frameworks`)

- **Decision**: Svelte 5 + TypeScript + Vite (svelte 5.59.1, @sveltejs/vite-plugin-svelte 7.3.2, vite 8.3.8, typescript 7.0.2; versões e licenças em R4). Vale também para `desktop-projects` e `desktop-processing`.
- **Rationale**: escolha do operador (block-003, dec-028).
- **Alternatives considered**: React 19, Vue 3, Solid, TypeScript sem framework.

### Decision 2: Versão mínima do macOS (P2, eixo `ambiente-alvo`)

- **Decision**: `bundle.macOS.minimumSystemVersion = "11.0"` (Big Sur).
- **Rationale**: escolha do operador (block-004, dec-029); resolve o conflito de R1, porque o Big Sur traz o WebKit do Safari 14, com WebP.
- **Alternatives considered**: manter o padrão 10.13 do Tauri (sem garantia de WebP).

### Decision 3: Limite do aviso de arquivo grande (P3, FR-034)

- **Decision**: 5 MB, aplicado como `LARGE_FILE_THRESHOLD_BYTES = 5_000_000` (MB decimal, leitura literal da resposta).
- **Rationale**: valor de projeto decidido pelo operador (block-005, dec-030). Não é fato externo.
- **Alternatives considered**: 1 MB, 10 MB.

### Decision 4: Configurações em arquivo JSON próprio, sem `tauri-plugin-store`

- **Decision**: `settings.json` gravado pelo lado Rust com `serde_json`, com escrita atômica (arquivo temporário + rename na mesma pasta).
- **Rationale**: o FR-023 exige detectar arquivo ilegível ou inválido e voltar aos padrões com aviso; com o arquivo próprio a validação fica em um só lugar e com teste em `cargo test`. Uma dependência a menos (plugin-store sai de R3/R4).
- **Alternatives considered**: `tauri-plugin-store` 2.5.1 (licença ok, mas a validação de esquema ficaria no front).

### Decision 5: Imagens lidas pelo lado Rust, nunca por URL de arquivo

- **Decision**: o webview nunca recebe caminho de disco. O comando `read_image` resolve o caminho relativo ao `.md`, confina à pasta aberta, confere extensão e assinatura (R8) e devolve os bytes com `tauri::ipc::Response`; o front cria um `Blob` e um `URL.createObjectURL`. A CSP libera `img-src` só para `'self'` e `blob:`.
- **Rationale**: FR-013, FR-032 e FR-041 ficam num único ponto testável em Rust; não é preciso abrir o protocolo de assets nem escopo de sistema de arquivos para o webview.
- **Alternatives considered**: protocolo de assets do Tauri com escopo (exige configurar escopo de leitura no webview, superfície maior); bytes como array JSON (lento para imagens grandes; fica como plano B se o tipo recebido no JS pelo `Response` não for `ArrayBuffer`).

### Decision 6: Pasta aberta guardada no lado Rust

- **Decision**: o comando `open_folder` valida e guarda a raiz canonizada no estado do app; os demais comandos recebem só caminhos relativos a ela.
- **Rationale**: o confinamento (FR-030, FR-032, FR-040) não depende do front repetir a raiz a cada chamada.

### Decision 7: Atalhos simbólicos nunca expandidos

- **Decision**: atalho para pasta aparece como item marcado e não expansível; atalho para arquivo `.md` abre só se o destino canonizado estiver dentro da pasta aberta.
- **Rationale**: atende o FR-030 (não seguir para fora, sem ciclo) sem detector de ciclo.

### Decision 8: Renderização em Web Worker, HTML bruto desligado e sanitização

- **Decision**: markdown-it com `html: false`, `linkify: false`, mais @vscode/markdown-it-katex com `throwOnError: false`; a renderização roda num Web Worker; o HTML resultante passa pelo DOMPurify (uso sob Apache-2.0) antes de entrar no DOM.
- **Rationale**: FR-017 (HTML bruto vira texto), FR-033 (fórmula inválida vira erro local), FR-029 (janela responsiva) e defesa em profundidade recomendada pela própria doc do KaTeX (R7).
- **Alternatives considered**: markdown-it-texmath 1.0.0 (também MIT; o plugin do VS Code tem a documentação de uso lida).

### Decision 9: Links

- **Decision**: link relativo para `.md` vira elemento clicável sem `href`; o clique chama `resolve_link`, que confina à pasta aberta. Qualquer outro link (externo, âncora, outros tipos) fica inativo, como texto estilizado sem `href`.
- **Rationale**: FR-040 e FR-015; sem `href` o webview não navega.

## A verificar no execute-task (sem fonte nesta data)

- Suporte a PNG, JPEG e GIF no webview (R2); o app filtra por assinatura de qualquer forma.
- Assinatura do JPEG (R8).
- Caminho exato da chave de CSP no `tauri.conf.json` (R6).
- Tipo recebido no JS quando o comando devolve `tauri::ipc::Response` (R6); se não for `ArrayBuffer`, aplicar o plano B da Decision 5.
- API do diretório de configuração do app no Tauri 2 (R6).
- Se a saída do KaTeX exige `'unsafe-inline'` em `style-src` (o exemplo oficial de CSP usa, sem explicar; R6, R7).
- Tabelas GFM ligadas por padrão no markdown-it (R7), coberto pelo teste do FR-011.
- Pasta onde o `.app` é gerado e forma de fixar o alvo `aarch64-apple-darwin` (R5, R6). Observar no primeiro build. **Resolvido em R9.10 (onda-011).**
- Pré-requisito Rust 1.90 ou mais novo: resolvido na onda-009 (cargo/rustc 1.100.0 instalados pelo operador).
- Nomes das regras do renderer do markdown-it (`image`, `link_open`, `link_close`) e a forma de sobrescrevê-las (Decision 8); R7 só cita as opções `html`, `linkify` e `validateLink`. Conferir na doc do markdown-it antes de escrever `markdown.ts`. **Resolvido em R9.9 (onda-011).**
- Se o pacote `katex` traz as fontes e como o Vite as empacota para servir de `'self'` (premissa do FR-015 e do Princípio I); se não bastar, a feature precisa de outra forma local de fornecer as fontes, nunca CDN. **Resolvido em R9.9 (onda-011).**
- Se o sistema de arquivos do macOS aceita um nome com caixa diferente da gravada (contracts/commands.md, `read_image`); o teste do FR-013 decide o comportamento real.
- Comando de desenvolvimento (`npm run tauri dev`) e scripts do `package.json` gerados pelo template do Tauri (só `tauri build` tem fonte, R5). **Parcial (R9.9): os scripts existem e o `build` foi executado; `tauri dev` não foi executado (exige janela).**
- Atributo `#[serde(rename_all = "camelCase")]` do serde e funções da std do Rust citadas como desenho (`canonicalize`, `to_lowercase`); conferir na doc do serde e da std ao implementar.
- Pasta de saída do Vite (`desktop/dist/`) usada no `.gitignore`; conferir na configuração gerada pelo template. **Resolvido em R9.9 (onda-011): `dist/`.**
- API Rust do `tauri-plugin-dialog` para abrir o diálogo de pasta a partir do lado Rust (contracts `open_folder`); se não existir, bloqueio humano, sem aceitar caminho vindo da interface.
- Mecanismo do Tauri 2 para restringir navegação e bloquear novas janelas no webview.
- Se o markdown-it aplica codificação percentual ao `src` de imagens e ao `href` de links (afeta nomes acentuados, FR-013). **Resolvido em R9.9 (onda-011): aplica; a interface decodifica.**
- Comportamento do DOMPurify fora de um DOM (Web Worker) e a forma de detectar que ele não pode sanitizar; o desenho já o coloca no thread principal. **Resolvido em R9.9 (onda-011): `DOMPurify.isSupported` é `false` sem DOM; `sanitizeHtml` devolve `null` e o visualizador mostra texto puro.**
- Se a CSP injetada pelo Tauri (nonces/hashes) anula `'unsafe-inline'` em `style-src`, o que afeta os estilos em linha do KaTeX (SC-003).
- Compatibilidade entre as versões de vite, @sveltejs/vite-plugin-svelte, vitest, svelte e typescript da tabela do plan (cada uma é a mais recente isolada) e versão mínima do Node. **Resolvido em R9.1 e R9.2 (onda-010).**

## R9. Verificações do execute-task (onda-010)

Cada linha traz a fonte observada. Execução real em 2026-11-24, macOS arm64 (`uname -m` → `arm64`).

### R9.1 Ambiente (tarefa 1.1)

| Item | Resultado | Fonte |
|------|-----------|-------|
| Rust | `cargo 1.100.0 (3acc79c0c 2026-11-05)`, `rustc 1.100.0 (0ef1bc386 2026-11-09)`; atende o MSRV 1.90 do `tauri` 2.12.7 | execução de `cargo --version` e `rustc --version` |
| Node.js e npm locais | Node `v24.22.2`, npm `11.19.1` | execução de `node --version` e `npm --version` |
| Versão mínima de Node na doc de pré-requisitos do Tauri | **Não declarada**: a página só recomenda baixar a versão LTS e mostra `v20.10.0` como exemplo de saída do terminal (não é requisito). Node só é necessário para frontends JavaScript. Também não declara versão mínima de Rust e cita "macOS Catalina (10.15) and later" para desenvolvimento | https://v2.tauri.app/start/prerequisites/ (página "Last updated: Aug 20, 2026") |
| Versão mínima de Node exigida pelas dependências do front | `vite` 8.3.8: `^20.19.0 \|\| >=22.12.0`; `@sveltejs/vite-plugin-svelte` 7.3.2: `^20.19 \|\| ^22.12 \|\| >=24`; `vitest` 5.1.1: `^22.12.0 \|\| ^24.0.0 \|\| >=26.0.0`; `jsdom` 31.0.1: `^22.22.2 \|\| ^24.15.0 \|\| >=26.0.0`; `svelte` 5.59.1: `>=18`; `typescript` 7.0.2: `>=16.20.0`; `@tauri-apps/cli` 2.12.6: `>= 10`. O mais restritivo é o `jsdom` (22.22.2, 24.15.0 ou 26). Node local 24.22.2 atende todos | `npm view <pacote>@<versão> engines --json` |
| `cargo-audit` | **Não instalado**: `cargo audit` → `error: no such command: audit`. A instalação via cargo foi bloqueada pela guarda de Bash do runtime (categoria package-manager); o operador aprovou a instalação (block-006), mas o comando precisa ser rodado por ele com `! cargo install cargo-audit` | execução; mensagem da guarda `bash-guard: BLOQUEADO — categoria=package-manager` |

### R9.2 Compatibilidade entre vite, plugin Svelte, vitest, svelte e typescript (tarefa 1.1.4)

Peer dependencies lidas com `npm view <pacote>@<versão> peerDependencies --json`; a condição "opcional" vem de `npm view <pacote>@<versão> peerDependenciesMeta --json` (conferido para vitest, vite e jsdom):

- `@sveltejs/vite-plugin-svelte` 7.3.2 exige `vite ^8.0.0-beta.7 || ^8.0.0` e `svelte ^5.46.4`: satisfeitos por vite 8.3.8 e svelte 5.59.1.
- `vitest` 5.1.1 aceita `vite ^6.4.0 || ^7.0.0 || ^8.0.0`: satisfeito por vite 8.3.8. Os demais peers (`jsdom`, `@types/node`, `happy-dom`, `@vitest/*`) são opcionais ou `*`.
- `vite` 8.3.8: os peers listados (`tsx`, `sass`, `terser`, `esbuild` etc.) são opcionais; não há peer sobre `svelte` nem `typescript`.
- `jsdom` 31.0.1: peer `@napi-rs/canvas ^1.0.13` (só para canvas; o app não usa).
- `svelte` 5.59.1 e `typescript` 7.0.2 não declaram peer dependencies.

Conclusão: as versões da tabela do plan são compatíveis nas peer dependencies declaradas; nenhuma versão foi alterada. A confirmação final é o `npm install` sem erro `ERESOLVE` (tarefa 1.2.2).

### R9.3 APIs do Tauri 2 conferidas no código-fonte baixado pelo cargo (tarefas 2.3.4, 2.4.1 a 2.4.4)

Fonte: crates `tauri` 2.12.7, `tauri-utils` 2.10.4, `tauri-plugin-dialog` 2.8.4 e `tauri-plugin-fs` 2.7.1 em `~/.cargo/registry/src/` (lidos nesta onda), mais o `cargo build` do esqueleto (compilou sem erro) e os esquemas gerados pelo `tauri-build` em `desktop/src-tauri/gen/schemas/`.

| Item | Resultado | Fonte observada |
|------|-----------|-----------------|
| 2.4.1 Diálogo de pasta pelo Rust | `use tauri_plugin_dialog::DialogExt;` e `app.dialog().file().blocking_pick_folder()` devolve `Option<FilePath>` (`None` se a pessoa fechou o diálogo); existe também `pick_folder(callback)`. `FilePath` (`Url` ou `Path`) tem `into_path() -> Result<PathBuf>`. O `blocking_*` não deve rodar na thread principal; o comando é `async`, então roda no executor assíncrono. A API Rust existe: `open_folder` sem argumentos é viável | `tauri-plugin-dialog-2.8.4/src/lib.rs` (`DialogExt`, `FileDialogBuilder::blocking_pick_folder`), `tauri-plugin-fs-2.7.1/src/file_path.rs` |
| 2.4.2 Diretório de configuração do app | `app_handle.path().app_config_dir() -> Result<PathBuf>`, resolvido como `config_dir()/<identifier>` (no macOS, `config_dir` vem do crate `dirs`) salvo `app > appDirectoriesOverride`. O diretório pode não existir: criar antes de gravar | `tauri-2.12.7/src/path/desktop.rs` |
| 2.4.3 Chave da CSP | `app.security.csp` em `tauri.conf.json`; aceita string única ou objeto diretiva → lista de fontes (`Csp::Policy` / `Csp::DirectiveMap`). Existe também `app.security.devCsp` (se ausente, a `csp` vale também no dev) e `freezePrototype` (padrão `false`). `bundle.macOS.minimumSystemVersion` tem padrão `"10.13"` (confirma R1) | `tauri-utils-2.10.4/src/config.rs` (`SecurityConfig`, `Csp`, `macos_minimum_system_version`) |
| 2.4.3 Nonces e hashes do Tauri | O Tauri só acrescenta nonce/hash a `script-src` e `style-src`, e só quando o HTML tem `<style>` ou `<script src="http…">`/scripts em linha (substitui os tokens `__TAURI_STYLE_NONCE__`/`__TAURI_SCRIPT_NONCE__`; se não há nonce nem hash, a diretiva fica como escrita). Não toca em atributos `style="…"`. Consequência: o HTML do Vite com CSS externo não recebe nonce em `style-src`; atributos `style` vindos de HTML injetado (saída do KaTeX) continuam regidos por `style-src` / `style-src-attr` do navegador. Se o KaTeX exige `'unsafe-inline'` é decisão a medir no webview real (tarefa 5.1 / quickstart) | `tauri-2.12.7/src/manager/mod.rs` (`replace_csp_nonce`), `tauri-utils-2.10.4/src/html.rs` e `html2.rs` (`inject_nonce`) |
| 2.4.4 Tipo recebido no JS com `tauri::ipc::Response` | `Response::new(Vec<u8>)` vira `InvokeResponseBody::Raw`; o script de IPC do webview (`ipc-protocol.js`) faz `response.arrayBuffer()` para qualquer `content-type` que não seja `application/json` ou `text/plain`. Logo o JS recebe `ArrayBuffer`. Confirmação em execução real fica no quickstart (cenário de imagem) | `tauri-2.12.7/src/ipc/mod.rs`, `tauri-2.12.7/scripts/ipc-protocol.js` |
| 2.3.4 Restringir navegação e bloquear novas janelas | `WebviewWindowBuilder::on_navigation(Fn(&Url) -> bool)` (o exemplo da doc do crate permite `url.scheme() == "tauri"` em produção e `localhost` só em dev) e `on_new_window(Fn(Url, NewWindowFeatures) -> NewWindowResponse)` com `NewWindowResponse::Deny`. Para aplicar a uma janela criada pela config, a janela precisa ser criada no `setup` (`WindowConfig.create = false` e `WebviewWindowBuilder::from_config`). Não suportado em Android/iOS (irrelevante aqui) | `tauri-2.12.7/src/webview/webview_window.rs` e `webview/mod.rs`; campo `create` em `config.rs` |
| 5.1.2 (insumo) Permissões do core | Os conjuntos `default` por módulo estão em `gen/schemas/acl-manifests.json` (ex.: `core:window` tem 28 permissões `allow-*`; `dialog` default = `allow-message`, `allow-save`, `allow-open`). Comandos próprios do app (`#[tauri::command]` registrados no `invoke_handler`) não aparecem como permissão do core | `desktop/src-tauri/gen/schemas/acl-manifests.json` (gerado pelo build) |

### R9.4 Limites do KaTeX (tarefa 2.3.1)

Fonte: https://katex.org/docs/options (página mostra v0.19.1): `maxExpand` (number, padrão 1000: "Limit the number of macro expansions to the specified number, to prevent e.g. infinite macro loops"); `maxSize` (padrão `Infinity`: tamanhos especificados pelo usuário são limitados a `maxSize` em `em`); `trust` padrão `false`; não há opção de profundidade documentada. Valor fixado no desenho: `maxExpand: 1000` explícito (o padrão documentado, para não depender de mudança futura do padrão) e `maxSize: 100` (valor de projeto, não fato externo: evita elementos arbitrariamente grandes por `\rule`). Ver plan.md, Endurecimento de segurança.

### R9.5 Formatos de imagem no webview e assinatura do JPEG (tarefa 2.4.7)

- PNG, JPEG, GIF e WebP: a página de compatibilidade do MDN (https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Formats/Image_types) lista PNG, JPEG e GIF com suporte em todas as versões de Chrome, Edge, Firefox, Opera e Safari; WebP no Safari exige "Safari 14 or later as well as macOS Big Sur (11) or a more recent version" (casa com R1 e a Decision 2). Fonte é o MDN, não a Apple; a confirmação no webview real do app entra no quickstart (7.1.5).
- Assinatura do JPEG: a Library of Congress (https://www.loc.gov/preservation/digital/formats/fdd/fdd000017.shtml) devolveu HTTP 403 e nenhuma outra fonte oficial foi lida. Observação por execução real: um JPEG gerado nesta máquina por `sips -s format jpeg` (a partir do `icon.png`) começa com os bytes `ff d8 ff e0 00 10 4a 46 49 46` (`xxd -l 16`; `file` reporta "JPEG image data, JFIF standard 1.01"). Regra adotada no código: o arquivo deve começar com `FF D8 FF` (o terceiro byte `FF` abrange os marcadores APPn dos JFIF/Exif). **Pendente de fonte oficial** para o caso Exif (`FF D8 FF E1`); só o caso JFIF foi observado. Item aberto, sem bloqueio: a regra só aceita ou recusa mais arquivos, não escreve dado externo.

### R9.6 Licenças das dependências transitivas cargo (tarefa 1.3.1)

Método: `cargo metadata --format-version 1 --filter-platform aarch64-apple-darwin --locked` sobre `desktop/src-tauri/Cargo.lock` (campo `license` de cada pacote). `Cargo.lock` tem 440 pacotes de terceiros (todas as plataformas); o grafo compilado para macOS arm64 tem 246.

| Licença declarada (grafo macOS arm64) | Pacotes |
|---------------------------------------|---------|
| MIT OR Apache-2.0 / Apache-2.0 OR MIT / MIT/Apache-2.0 / Apache-2.0 / MIT (variações de escrita) | 113 + 29 + 7 + 1 + 1 + 39 (MIT) |
| Unicode-3.0 (família `icu_*`, `idna`, etc.) | 18 (+1 `(MIT OR Apache-2.0) AND Unicode-3.0`) |
| Zlib OR Apache-2.0 OR MIT e variantes (há opção MIT/Apache) | 9 + 2 + 2 |
| Unlicense OR MIT / Unlicense/MIT (opção MIT) | 7 + 2 |
| MPL-2.0 | 5: `cssparser` 0.37.0, `cssparser-macros` 0.7.1, `dtoa-short` 0.3.5, `option-ext` 0.2.0, `selectors` 0.38.0 |
| BSD-3-Clause, BSD-3-Clause AND MIT, BSD-3-Clause/MIT | 2 + 1 + 1 |
| Zlib | 2 |
| 0BSD OR MIT OR Apache-2.0; CC0-1.0 OR MIT-0 OR Apache-2.0; Apache-2.0 AND MIT; Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT | 1 cada |

- **GPL/AGPL obrigatória: nenhuma** no grafo macOS. (No `Cargo.lock` completo há 2 pacotes `MIT OR Apache-2.0 OR LGPL-2.1-or-later`, de outras plataformas e com opção MIT/Apache; não entram no build macOS.)
- **Governança (block-008, resolvido)**: a emenda 1.2.0 da constitution (PR #8, já no `main`) alterou o Princípio II: dependência de código, direta ou transitiva, pode ter qualquer licença permissiva sem copyleft (MIT, Apache, BSD, ISC, Zlib, 0BSD, Unicode-3.0 ou equivalente); dependência transitiva pode ter copyleft fraco no nível de arquivo (MPL-2.0) quando exigida por dependência direta permissiva, sem modificação, com nome e licença registrados no `plan`. Decisão do operador: aceitar. O registro nominal dos 23 crates está em [plan.md](./plan.md) §"Licenças transitivas registradas (Princípio II, constitution 1.2.0)"; a lista abaixo é a fonte (saída de `cargo metadata`, não digitada de memória).
- **Crates MPL-2.0 (5)**, todos sem modificação e exigidos por dependência direta permissiva (`cargo tree --target aarch64-apple-darwin --locked -i <crate>`): `cssparser` 0.37.0, `cssparser-macros` 0.7.1, `dtoa-short` 0.3.5 e `selectors` 0.38.0 (via `dom_query` 0.28.0 → `tauri-utils` 2.10.4 → `tauri` 2.12.7); `option-ext` 0.2.0 (via `dirs-sys` 0.5.0 → `dirs` 7.0.0 → `tauri` 2.12.7 e `wry` 0.57.3).
- **Crates Unicode-3.0 (18)**: `icu_collections`, `icu_locale_core`, `icu_normalizer`, `icu_normalizer_data`, `icu_properties`, `icu_properties_data`, `icu_provider` (todos 2.4.0), `litemap` 0.8.3, `potential_utf` 0.1.6, `tinystr` 0.8.5, `writeable` 0.6.4, `yoke` 0.8.4, `yoke-derive` 0.8.4, `zerofrom` 0.1.8, `zerofrom-derive` 0.1.8, `zerotrie` 0.2.5, `zerovec` 0.11.9, `zerovec-derive` 0.11.7. Entram por `url` 2.5.9 → `idna` 1.1.0 → `idna_adapter` → `icu_normalizer`/`icu_properties` (cadeia do `tauri-utils`/`tauri`). Mais 1 pacote `(MIT OR Apache-2.0) AND Unicode-3.0` (`unicode-ident` 1.0.27), cuja parte Unicode-3.0 também é permissiva.
- O relatório por pacote fica reproduzível com o comando acima (nenhuma ferramenta de licença foi instalada; `cargo-license` e `cargo-deny` não estão instalados).

### R9.7 Resultados de execução do núcleo Rust (onda-010)

- `cargo test` em `desktop/src-tauri`: 74 testes, 0 falhas (`test result: ok. 74 passed`), incluindo 5.000 itens no primeiro nível (SC-002) dentro de 2 s e concorrência de gravações de configuração; `cargo clippy --all-targets -- -D warnings` e `cargo fmt --check` sem achados.
- `#[serde(rename_all = "camelCase")]`, `std::fs::canonicalize` e `str::to_lowercase` (tarefa 2.4.8) compilam e são exercitados pelos testes (contrato camelCase em `model::tests`, confinamento em `paths::tests`, ordem sem caixa em `fs_tree::tests`).
- O binário `target/debug/artigo2md-desktop` iniciou e permaneceu vivo por mais de 30 s (encerrado manualmente com `pkill`, código 144): o `setup` (estado, janela criada por `from_config` com `on_navigation`/`on_new_window`) não falhou. Janela, diálogo de pasta e tráfego de rede ainda não foram observados (cenários 1, 7 e 8 do quickstart, 7.1.5).
- Efeito do teste de contrato: `cargo test` grava `desktop/src-tauri/target/contract-samples.json` com um exemplo de cada retorno e dos erros (`detail` sempre `null`), para o `contract.test.ts` (tarefa 4.1.4).

### R9.8 Licenças e vulnerabilidades das dependências npm e cargo (tarefas 1.3.2 e 1.3.4, onda-011)

**Instalação npm** (feita pelo operador no block-007, que a guarda de Bash nega ao agente): 105 pacotes em `desktop/node_modules`, `desktop/package-lock.json` gerado. `npm ci --dry-run` resolve o lockfile sem erro.

**Licenças npm** (campo `license` de cada pacote em `package-lock.json`, 166 entradas incluindo binários opcionais de outras plataformas; 35 de produção e o restante (131) de desenvolvimento). Comando: `jq` sobre `.packages` do `package-lock.json`; confirmado com `npm view <pacote>@<versão> license` nos casos fora de MIT/Apache.

| Licença | Pacotes | Observação |
|---------|---------|------------|
| MIT | 28 de produção, 75 de desenvolvimento | |
| Apache-2.0 / Apache-2.0 OR MIT / MIT OR Apache-2.0 | 4 de produção (`@tauri-apps/api`, `@tauri-apps/plugin-dialog`, `aria-query` 5.3.1 e `axobject-query` 4.1.0, estes dois via `svelte`), 36 de desenvolvimento (incl. `@tauri-apps/cli*`, `typescript`) | |
| BSD-2-Clause | `entities` 8.1.0 (produção, via `markdown-it`), `entities` 9.0.0 e `webidl-conversions` 8.0.1 (desenvolvimento) | |
| BSD-3-Clause | `source-map-js` 1.2.4, `tough-cookie` 6.1.0 (desenvolvimento) | |
| (MPL-2.0 OR Apache-2.0) | `dompurify` 3.5.0 (produção) | usada sob Apache-2.0 |
| PSF-2.0 | `argparse` 3.0.4 (produção, transitiva de `markdown-it`) | permissiva, sem copyleft; registrada no plan.md |
| ISC | `picocolors` 1.1.1, `saxes` 6.0.0 (desenvolvimento) | |
| 0BSD | `tslib` 2.9.1 (desenvolvimento) | |
| BlueOak-1.0.0 | `lru-cache` 11.5.3 (desenvolvimento) | permissiva |
| MPL-2.0 | `lightningcss` 1.33.0 e 11 binários por plataforma (desenvolvimento, via `vite`) | ferramenta de build, não entra no `.app`; registrada no plan.md |

GPL/AGPL: **nenhuma**. Pacote sem licença declarada: nenhum.

**`npm audit`** (2 achados, severidade baixa): `katex` 0.11.0 a 0.18.1, "Existing prototype pollution can bypass trust restrictions" ([GHSA-238p-pmpm-9mq7](https://github.com/advisories/GHSA-238p-pmpm-9mq7)), sem correção disponível; vem da cópia aninhada `katex` 0.16.47 dentro de `@vscode/markdown-it-katex` 1.1.2 (que declara `katex ^0.16.4`). O `katex` direto do projeto (0.19.1) não está na faixa afetada.

Tratamento (decisão do operador no block-007: avaliar trocar o plugin ou registrar risco aceito; decisão dec-052):

1. O plugin aceita a instância de KaTeX pela opção `katex` (`node_modules/@vscode/markdown-it-katex/dist/index.js`: `const katex = options?.katex ?? katex_1.default;`). `markdown.ts` passa o `katex` direto (0.19.1) com `trust: false`, `maxExpand` e `maxSize`: toda renderização usa a versão não afetada.
2. `vite.config.ts` fixa `resolve.dedupe: ["katex"]`, o que faz o Vite resolver `katex` sempre a partir da raiz do projeto. Resultado verificado: o bundle do Worker (`dist/assets/render.worker-*.js`) não contém `0.16` (a cópia 0.16.47 deixou de ser empacotada) e traz só `katex` 0.19.1.
3. Sem troca de dependência npm: nenhuma nova instalação. O achado continua listado pelo `npm audit` porque a cópia aninhada segue no `node_modules`/lockfile (ferramenta de desenvolvimento e testes; não vai ao `.app`). **Risco residual aceito**: severidade baixa, código afetado não executado nem empacotado, e `trust` desligado. Melhoria opcional que exigiria nova instalação pelo operador: `overrides` em `package.json` apontando o `katex` do plugin para o de topo, depois `npm install`; não aplicada porque o plugin declara `^0.16.4` e a combinação com 0.19 não tem fonte oficial.

**`cargo audit`** (cargo-audit 0.23.0, `cargo audit` sobre `desktop/src-tauri/Cargo.lock`, 441 dependências, 1.567 avisos carregados do advisory-db): **0 vulnerabilidades**, 3 avisos (não vulnerabilidades): `chrono` 0.4.45 (RUSTSEC-2026-0543, unmaintained), `proc-macro-error` 1.0.4 (RUSTSEC-2024-0370, unmaintained) e `glib` 0.18.5 (RUSTSEC-2024-0429, unsound). Nenhum dos três está no grafo de macOS arm64 (`cargo tree --target aarch64-apple-darwin -i <crate>` responde "nothing to print"); vêm de alvos Linux/Windows do Tauri. Nada a corrigir.

### R9.9 Resultados de execução da interface (onda-011)

| Item | Resultado | Fonte observada |
|------|-----------|-----------------|
| 1.2.3 Scripts do `package.json` | `dev` (`vite`), `build` (`vite build`), `test` (`vitest run`), `tauri` (`tauri`); a pasta de saída do Vite é `desktop/dist/` (`dist/index.html` e `dist/assets/`), igual a `build.frontendDist` (`../dist`) | `npm run build` e `ls dist` |
| 2.4.5 Regras do renderer | `md.renderer.rules.image`, `link_open` e `link_close` existem e são substituídas (teste `markdown.test.ts`); `html: false`, `linkify: false` | vitest |
| 2.4.5 Tabelas GFM | Ligadas por padrão no preset `default` do markdown-it 15.0.3: `| a |...|:--|:-:|--:|` gera `<th>`/`<td style="text-align:left|center|right">` | vitest (`markdown.test.ts`) |
| 2.4.5 Codificação percentual | `src` e `href` chegam codificados (`imagens/f%C3%ADgura%201.png`, `Pasta/%C3%A1%20b.md`); `decodeReference` os devolve a `imagens/fígura 1.png`; a interface decodifica antes de `read_image` e `resolve_link` | vitest |
| 2.4.6 Fontes do KaTeX | O pacote `katex` traz as fontes em `dist/fonts/` e o CSS (`katex/dist/katex.min.css`) as referencia por `url()`; com `build.assetsInlineLimit: 0` o Vite as emite como arquivos em `dist/assets/KaTeX_*.{woff2,woff,ttf}` e o CSS final usa `url(/assets/KaTeX_…)`, sem `data:` e sem endereço externo (`grep` no CSS e no JS final: nenhuma URL `http(s)` além de URIs de namespace e links de erro do Svelte, que não são requisições) | `npm run build`, `grep` em `dist/` |
| 2.4.3 `unsafe-inline` e KaTeX | A saída do KaTeX usa atributos `style="…"`. O Tauri não toca em atributos `style` (R9.3), então `style-src 'self'` bloquearia o atributo vindo de HTML. Desenho adotado: `toFragment` (sanitize.ts) lê o HTML sanitizado em um `<template>` (documento inerte), aplica cada declaração pelo CSSOM (`element.style.setProperty`, permitido pela CSP) antes de inserir no DOM; `style` só passa se for declaração simples sem `url()`/`@import`. A CSP de produção fica **sem** `'unsafe-inline'`. O efeito no webview real (WebKit) só é confirmável com o app aberto (cenário do quickstart, 7.1.5) | `sanitize.test.ts`, `security-config.test.ts` |
| 5.1.1 CSP | Gravada em `app.security.csp` e `app.security.devCsp` (a de desenvolvimento acrescenta `'unsafe-inline'` só em `style-src` e `localhost:1420` para o Vite) | `tauri.conf.json`, `security-config.test.ts` |
| 5.1.2 Capabilities | `capabilities/default.json` só para a janela `main`, `permissions: []`. Comandos próprios do app (`#[tauri::command]`) de origem local não exigem permissão quando o app não define manifesto de ACL; só comandos de plugin (`plugin:…`) e origens remotas passam pelo ACL | `tauri-2.12.7/src/webview/mod.rs` (bloco "Check ACL on plugin commands, when the app defined its ACL manifest, or when the request comes from a non-local (remote) origin"), `cargo build` com a capability |
| 4.2.3 `DOMPurify.isSupported` | `sanitizeHtml` devolve `null` (e o visualizador mostra o documento como texto puro, com o aviso `renderError`) se `isSupported` for `false` ou se a sanitização lançar erro; testado com `isSupported` simulado em `false` e `sanitize` lançando | `sanitize.test.ts`, `components.test.ts` |
| Alvo dos navegadores | `build.target: "safari14"` (o padrão do Vite 8, "baseline-widely-available", é Chrome 111+/Safari 16.4+, segundo `vite/dist/node/index.d.ts`) para casar com o piso macOS 11 (R1, Decision 2). O Worker é clássico (`new Worker(new URL(...))` sem `type: "module"`; o Vite o empacota como IIFE) para não depender de Workers de módulo. Nenhum dos dois pôde ser exercitado num WebKit de macOS 11 (só nesta máquina) | `vite.config.ts`, `dist/assets/render.worker-*.js` (IIFE) |

### R9.10 Build do `.app` (tarefas 6.1.1 a 6.1.4, onda-011)

| Item | Resultado | Fonte observada |
|------|-----------|-----------------|
| 6.1.1 Comando e pasta de saída | `npm run tauri build -- --bundles app` (host `aarch64-apple-darwin`) gera `desktop/src-tauri/target/release/bundle/macos/artigo2md.app` (12,57 MiB). Com `--target aarch64-apple-darwin` a saída é `desktop/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/artigo2md.app`; esse é o comando documentado no README, porque fixa o arm64 mesmo se o Mac for outro | execução dos dois builds (`Finished 1 bundle at: …`) |
| Arquitetura e piso | `lipo -archs` → `arm64`; `Info.plist` → `LSMinimumSystemVersion` = `11.0`; `CFBundleIdentifier` = `br.com.artigo2md.desktop`; recursos: só `artigo2md.icns` (os arquivos do front ficam embutidos no executável) | `lipo`, `plutil` |
| 6.1.2 Assinatura | Nenhuma identidade exigida nem configurada; build concluído sem conta Apple. `codesign -dv` → `Signature=adhoc`, `flags=0x20002(adhoc,linker-signed)`; `codesign --verify` do bundle responde "code has no resources but signature indicates they must be present" (esperado para o `.app` sem assinatura de bundle; o executável abre) | execução de `codesign` |
| 6.1.3 SHA-256 | Executável `371f62a1…a4fc0` e manifesto `b8417c82…77d` (valores completos no README de `desktop/`). Dois builds do mesmo código em pastas de saída diferentes (com e sem `--target`) geraram executáveis com hashes diferentes: o build não é reprodutível bit a bit entre pastas | `shasum -a 256` |
| 6.1.4 Abertura | Build final (`--target aarch64-apple-darwin`): `open -n artigo2md.app` e execução direta do executável; o processo `artigo2md-desktop` subiu e continuou vivo por mais de 9 s (`ps` etime), sem nada em stderr; nenhum socket de rede aberto pelo processo (`lsof -nP -a -p <pid> -i` sem linhas) e nenhum log de erro criado em `~/Library/Logs/br.com.artigo2md.desktop`. (Numa tentativa intermediária o `pgrep` não achou o processo por padrão de busca incorreto; repetido, vivo.) Os processos de rede do WebKit (`com.apple.WebKit.Networking`) são compartilhados com o sistema e não atribuíveis a este app, então o SC-004 por observação de tráfego continua **pendente** (7.1.5). **A janela não pôde ser vista**: `screencapture` falhou ("could not create image from display") e `osascript` para o System Events não respondeu (sem acesso à tela nesta sessão). Confirmação visual da janela principal, do carregamento do JS/CSP e das fontes do KaTeX no WebKit real fica pendente de execução humana (cenários 1 a 8 do quickstart) | `open`, `ps`, `lsof` |

### R9.11 Pendências para converge e review (onda-011)

Registradas, não resolvidas por estarem fora do escopo desta onda:

- **Caminho absoluto no webview** (resolvido na onda-013, tarefa 8.1.1): `get_startup_state`, `save_settings` e `reset_settings` devolviam `AppSettings` com `lastFolder` absoluto, contra "nenhum caminho absoluto vai ao webview" (plan.md, Fronteira de confiança). Agora devolvem `UserSettings` (sem `lastFolder`); `types.ts`, o contrato e o data-model foram alinhados.
- **`save_settings` recebe JSON cru** (`serde_json::Value`) para sempre devolver `invalidSettings`; `api.ts` continua tipando `UserSettings`.
- **Ícone provisório**: `icons/icon.png` único; o bundler gerou `artigo2md.icns` a partir dele.
- **Origem `tauri://localhost`** de produção: não confirmada no webview real (a navegação permitida depende dela, lib.rs).
- **Parciais**: 2.4.3 (efeito do `style` do KaTeX sob a CSP no WebKit real), 2.4.7 (Exif/JPEG e PNG/GIF no webview), 6.1.4, 7.1.5 e 7.1.6 (tudo que exige a janela real: abrir a janela, cenários 1 a 8 do quickstart, observação de tráfego, revisão visual das telas, SC-001 com pessoas, SC-002 de renderização de 5.000 itens no DOM).
- **`@tauri-apps/plugin-dialog` (npm)** estava nas dependências (plan.md) mas a interface não o usa: o diálogo é aberto pelo Rust. Decisão da onda-013 (tarefa 8.2.1): remover. A crate `tauri-plugin-dialog` (Cargo) fica, pois `commands.rs` e `lib.rs` a usam. **Executada pelo operador na onda-014** (`npm uninstall @tauri-apps/plugin-dialog` em `desktop/`; saída do npm informada: "added 1 package, removed 1 package, and audited 106 packages" e "2 low severity vulnerabilities", as mesmas do `katex` aninhado já aceitas em R9.8). Verificação: `grep` por `plugin-dialog` em `desktop/package.json` e `desktop/package-lock.json` sem ocorrências; `node_modules/@tauri-apps` contém `api`, `cli`, `cli-darwin-arm64`; `npm ls --omit=dev --depth=0` lista 6 dependências de produção (sem o plugin); `npm test` 131 passed (11 arquivos), `npx tsc --noEmit` sem erros, `npm run build` concluído e `uv run pytest` 106 passed, 4 skipped. A contagem do relatório de licenças acima (4 de produção) é a de antes da remoção e passa a 3. **Aviso do npm sobre `fsevents@2.3.3`** ("install scripts present", não coberto por `allowScripts`): `npm ls fsevents` mostra uma única origem, `vite@8.3.8 → fsevents@2.3.3`, ou seja, vem da cadeia de ferramentas de desenvolvimento do Vite; não é dependência de produção (não consta em `npm ls --omit=dev`) e não vai ao `.app`. Nada a fazer; não foi investigado o conteúdo do script de instalação do pacote.
- **`npm overrides` opcional** para o `katex` do plugin (R9.8): exige nova instalação pelo operador.
- **Gate `uv run pytest`**: passou (106 passed, 4 skipped) no ambiente já sincronizado com `uv sync --extra dev` (onda-010); num checkout limpo sem o extra `dev`, o CLAUDE.md pede `uv sync --extra dev` antes. Não mascarado: o pré-requisito é do projeto Python, não desta feature.
- **`codesign --verify` do bundle** reclama de "code has no resources but signature indicates they must be present" (assinatura ad hoc do linker só no executável); o app abre. Se a feature de release (`desktop-release`) adicionar assinatura, rever.

### R9.12 Falha de renderização relatada no block-010 (tarefas 9.1.1 a 9.2.2, onda-017)

Sintoma relatado pelo operador: todo `.md`, inclusive Markdown simples, abre com o aviso "Não foi possível renderizar este documento" e aparece como texto. Suspeita inicial (não confirmada): carga do `render.worker-*.js` sob `worker-src 'self'` com a origem `tauri://localhost`.

**Confirmado**

| Item | Resultado | Fonte observada |
|------|-----------|-----------------|
| Suspeita da CSP | **Descartada.** Em produção o Worker é clássico (`new Worker(new URL(\`/assets/render.worker-….js\`, ``+import.meta.url))`, sem `blob:`/`data:`/`importScripts`), servido pelo esquema `tauri://localhost` e carregado sob `worker-src 'self'`: renderiza `security.md` (7.455 caracteres de HTML) e um `.md` simples. Reproduzido em 4 montagens: WKWebView com esquema `tauri` e os cabeçalhos do Tauri (CSP de produção, com e sem os hashes `sha256-` que o Tauri injeta em `script-src`); o runtime real Tauri 2.12.7/wry 0.57.3 (release, `tauri/custom-protocol`) com o `dist` atual; o mesmo runtime com os assets embutidos no `.app` de 00:20 (extraídos de `tauri-codegen-assets`, Worker com o mesmo SHA-256 do atual); e esse binário dentro de uma cópia do bundle aberta por `open` com janela visível | harness Swift/WKWebView e cópia de diagnóstico do app em pasta temporária (fora do projeto); `tauri-2.12.7/src/protocol/tauri.rs` e `src/manager/mod.rs` (CSP só no HTML, `Content-Type: text/javascript` para `.js`); `tauri-codegen-2.7.6/src/embedded_assets.rs` (hashes só em `script-src`; nada em `worker-src`) |
| Modo das sessões do operador | As duas sessões do teste (21:04 e 21:11, PIDs 97870 e 18304) têm a assinatura do **modo de desenvolvimento**: `DeveloperExtrasEnabled=true`, página e recursos carregados pelo `com.apple.WebKit.Networking` (servidor do Vite) e "missing main bundle identifier" — igual ao `tauri dev` (PID 44509) e diferente do `.app` release (sem DeveloperExtras, recursos só pelo `WKURLSchemeHandler`, com bundle id). O unified log consultado (00:00 a 22:12) não mostra outra abertura do app entre 00:22 e 22:11 além dessas duas (a retenção do log pode não guardar tudo) | `log show` (unified log) filtrado por `process == "artigo2md-desktop"`, contagem de `scheduled with the NetworkProcess`, `tryLoadingUsingURLSchemeHandler`, `DeveloperExtrasEnabled` |
| Causa | Em desenvolvimento o Vite não empacota o Worker: `new Worker(new URL("./render.worker.ts", import.meta.url))` sem `type` vira `render.worker.ts?worker_file&type=classic`, servido como `importScripts("/@vite/env")` seguido de `import { renderMarkdown } from "/src/lib/markdown.ts"`. Worker clássico não aceita `import` ESM: o WebKit dispara `error` com "SyntaxError: Unexpected token '{'. import call expects one or two arguments." e o `RenderSession` rejeita em todo documento | `vite/dist/node/chunks/node.js` (transform `worker_file`, `importScripts` para `classic`); `server.transformRequest` do Vite com a config do projeto; Tauri real em modo dev (`devUrl` numa porta livre) com a correção desfeita: `worker-error` acima em `render.worker.ts?worker_file&type=classic:5:0` e o visualizador em texto puro |
| Correção | `defaultWorkerFactory` cria o Worker com `{ type: "module" }` só quando `__RENDER_WORKER_AS_MODULE__` é `true`, constante definida em `vite.config.ts` como `command === "serve"` (servidor do Vite; independe do `NODE_ENV`). No build ela vale `false`, o ramo some e o `dist` mantém a instanciação clássica de antes. CSP de produção e `devCsp` **inalteradas** (nenhum `blob:`, `'unsafe-inline'` ou `'unsafe-eval'` acrescentado). Mesmo Tauri dev com a correção: `type=module`, `worker-message ok`, HTML renderizado nos dois arquivos | Tauri real em modo dev; `dist/assets/index-*.js` após `npm run build` (uma instanciação, sem `type:"module"`) |
| Alternativas avaliadas | (a) `type: "module"` em todos os modos: mudaria a produção; um IIFE roda como Worker de módulo no WebKit desta máquina, mas não há fonte sobre o macOS 11 (Safari 14, piso da Decision 2) com Workers de módulo — mantido clássico (R9.9). (b) `blob:` em `worker-src`: desnecessário, a CSP não é a causa. (c) Renderizar no thread principal se o Worker falhar: mudaria o desenho de segurança do plan.md e esconderia a falha | dec-069 |
| Detalhe técnico na interface | O aviso passa a trazer "Detalhe técnico: <etapa>: <mensagem>" (`workerCreate`, `workerError` com arquivo:linha:coluna do `ErrorEvent`, `messageError`, `renderError` com a exceção do Worker, `sanitize` com o motivo do DOMPurify), cortado em 300 caracteres e também enviado ao `console.error`. Não contém caminho do disco: o arquivo citado é a URL do Worker (`tauri://localhost/…` ou `http://localhost:…`) | `render.ts`, `sanitize.ts`, `Viewer.svelte`; `render.test.ts`, `components.test.ts`, `sanitize.test.ts` |
| Teste que pega o caso | `tests/worker-build.test.ts`: (1) pede ao Vite o `render.ts` e o Worker no tipo que `defaultWorkerFactory` usa no servidor do Vite e exige que um Worker clássico nunca receba `import` ESM — **falha com o código anterior** (classic + `import { renderMarkdown }`) e passa com a correção (verificado desfazendo a correção e rodando de novo); (2) roda o `vite build` (sem gravar) e exige uma única instanciação clássica de `/assets/render.worker-*.js`, Worker IIFE sem `import`/`export`/`importScripts` e `worker-src` = `'self'` na CSP de produção | `npx vitest run tests/worker-build.test.ts` |
| `.app` novo | `desktop/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/artigo2md.app`, executável de 2026-11-25 22:39:04, SHA-256 `7c7ce947b88ee3893f8c64a7916a78a7e3e0b0cef71edccbbaffe678e4fa7dad` (manifesto `bc130aa7…ecd8a`). O Worker embutido (extraído de `tauri-codegen-assets`) tem o mesmo SHA-256 do `dist` atual (`a72e7261…e5b7`). Aberto por `open -n`: processo vivo após 10 s, 0 sockets de rede (`lsof -i`), assinatura de release no log (sem DeveloperExtras, recursos só pelo esquema `tauri`) | `tauri build`, `shasum`, `ps`, `lsof`, `log show` |

**Hipótese (não confirmada)**

- Que o operador também tenha visto a falha no `.app` release: o log consultado não mostra abertura do `.app` release no período do teste; todas as montagens de produção acima renderizam. Se a falha aparecer no `.app` novo, o detalhe técnico do aviso diz em qual etapa ela ocorre.
- O comportamento no macOS 11 (Worker clássico de produção) continua sem verificação nesta máquina (R9.9).

### R9.13 Assinatura ad hoc do bundle e Gatekeeper (block-011, tarefas 6.1.4 e 10.1.1 a 10.3.1, onda-018)

Sintoma relatado pelo operador (block-011): o `.app` do build de 2026-11-25 22:39 com atributo de quarentena é recusado pelo macOS como danificado, sem a opção "Abrir Mesmo Assim". Evidência dele: `codesign -dv` com `flags=0x20002(adhoc,linker-signed)`, `Info.plist=not bound`, `Sealed Resources=none`; `codesign --verify --deep --strict` falha com "code has no resources but signature indicates they must be present". Uma cópia assinada com `codesign --force --deep -s -` (Info.plist entries=14, Sealed Resources version=2) validou e abriu pelo caminho do Gatekeeper (`AppTranslocation` observado).

**Confirmado**

| Item | Resultado | Fonte observada |
|------|-----------|-----------------|
| Reprodução do sintoma | A cópia do bundle gerado antes da correção (scratchpad), sem a chave de assinatura: `flags=0x20002(adhoc,linker-signed)`, `Info.plist=not bound`, `Sealed Resources=none`; `codesign --verify --deep --strict` e `spctl -a -vv` falham com "code has no resources but signature indicates they must be present" | `codesign -dv`, `codesign --verify`, `spctl -a -vv` nesta máquina |
| Fonte do bundler | O código do `tauri-bundler` **não está** em `~/.cargo/registry` (está compilado dentro do `@tauri-apps/cli` 2.12.6; o crate `tauri` do runtime é 2.12.7). A chave `bundle.macOS.signingIdentity` ("Identity to use for code signing.", `string`/`null`) vem do `config.schema.json` do CLI; o `man codesign` define `-` como identidade ad hoc. Nenhuma outra chave foi usada | `node_modules/@tauri-apps/cli/config.schema.json`; `strings` no binário nativo do CLI (variáveis `APPLE_SIGNING_IDENTITY`, `--no-sign`) |
| Tratamento de `"-"` | Confirmado no build real, não por leitura de fonte: `npm run tauri build -- --bundles app --target aarch64-apple-darwin` imprime `Signing with identity "-"` para o executável e para o `.app` ("replacing existing signature") e avisa que pula a notarização (sem credenciais) | saída do build |
| Resultado no bundle | `flags=0x10002(adhoc,runtime)` (o runtime reforçado vem do padrão do bundler), `Info.plist entries=14`, `Sealed Resources version=2 rules=13 files=1`, `Identifier=br.com.artigo2md.desktop`; `codesign --verify --deep --strict --verbose=2`: "valid on disk" e "satisfies its Designated Requirement"; `spctl -a -vv`: "rejected" (resposta esperada de app sem desenvolvedor identificado, distinta de "code has no resources") | `codesign -dv`, `codesign --verify`, `spctl` |
| Execução | `open -n` do bundle novo: processo vivo após 10 s, 0 sockets (`lsof -i`), sem encerramento pelo sistema; encerrado com `kill` no PID | `ps`, `lsof` |
| Verificação no build | `npm run build:app` = `tauri build --bundles app --target aarch64-apple-darwin && npm run verify:app`; `verify:app` = `codesign --verify --deep --strict --verbose=2 <.app>`. Contra o bundle novo: exit 0. Contra a cópia do bundle antigo (só assinatura do linker) no scratchpad, o mesmo comando: exit 1 com "code has no resources but signature indicates they must be present" | execução do script e do comando equivalente |
| Hashes | Executável `1f387adc525f0996e30549c9a18c395818bf02ee1b3bfe00c96cc51c5b793922`, manifesto `a64ffdcfb7e9fe33950278a71432949133f20048e1620fcd64b8a63a9e8bd1b2` (build de 2026-11-26 08:28). Dois builds seguidos na mesma pasta deram os mesmos valores | `shasum -a 256` |

**Não verificado**

- O build novo (com `flags=…(adhoc,runtime)`) não foi aberto pelo operador sob quarentena real: o que o operador abriu foi uma cópia assinada com `codesign --force --deep -s -`, sem a flag `runtime`. A estrutura selada (Info.plist e recursos) é a mesma; o que muda é a flag de runtime reforçado. Aqui só foi possível checar a validade da assinatura, o `spctl` e a subida do processo. Se o Gatekeeper tratar o bundle novo diferente do que o operador viu, a alternativa é desligar o runtime reforçado (chave `hardenedRuntime` do mesmo bloco `macOS` no schema do CLI), o que exigiria novo build e nova abertura pelo operador.
- A especificação (FR-026) diz "sem assinatura": lê-se como "sem assinatura de desenvolvedor identificado". Nenhuma conta Apple, certificado ou notarização é exigido.

