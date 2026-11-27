# artigo2md desktop (esqueleto)

App desktop macOS (Apple Silicon) do artigo2md: janela com barra lateral, árvore somente leitura de uma pasta
escolhida, visualizador de Markdown (tabelas GFM, fórmulas LaTeX, imagens locais) e configurações. Feita com
Tauri 2 (núcleo Rust) e Svelte 5 + TypeScript. **Sem serviços externos**: nenhuma conexão sai da máquina, exceto os
downloads de imagens Docker e de modelos que você confirma na primeira execução da tela "Infraestrutura"; a CSP da
interface fica restrita a `'self'` e `blob:` e o núcleo Rust só fala com `127.0.0.1`. Specs, planos e decisões:
`docs/specs/desktop-shell/` (o app em si) e `docs/specs/desktop-stack-control/` (a tela "Infraestrutura").

A CLI, a interface web e a suíte Python (`src/artigo2md/`, `tests/`) não são alteradas por esta pasta.

## Pré-requisitos

- macOS 11.0 ou mais novo, somente Apple Silicon (arm64). Intel e binário universal estão fora desta feature.
- Rust 1.90 ou mais novo (testado com `rustc` 1.100.0), instalado pelo `rustup`; depois `. "$HOME/.cargo/env"`.
- Node.js e npm (testado com Node 24.22.2 e npm 11.19.1; `vite` 8 pede Node `^20.19.0 || >=22.12.0`).
- Instalar as dependências do front, sempre com o lockfile: `npm ci` (em `desktop/`).

## Infraestrutura (tela "Infraestrutura")

A entrada "Infraestrutura" da barra lateral controla, sem terminal, a infraestrutura local do repositório
(`stack/`: Postgres, RabbitMQ e API do app) e o pool de workers de conversão (`artigo2md worker`). Ela:

- diagnostica o runtime Docker (Docker Desktop, OrbStack ou colima, com o Docker Compose) e, se faltar, mostra o
  guia de instalação;
- inicia e para a infraestrutura (`stack/up.sh` e `stack/down.sh`; os dados nos volumes são mantidos) e mostra o
  estado de cada serviço;
- na primeira execução, lista o que será baixado (imagens e modelos de conversão) e só baixa depois da sua
  confirmação; cria o `stack/.env` com senhas geradas (as senhas nunca aparecem na tela nem no registro do app);
- inicia e para o pool de workers e encerra os workers ao fechar o app.

Pré-requisitos desta tela, além dos do app: o repositório do artigo2md clonado (indique a pasta em
**Configurações > Pasta do repositório**), `uv` (https://docs.astral.sh/uv/) e Docker com o plugin `docker compose`.
O painel de gestão do RabbitMQ não é publicado; só o terminal (`sh stack/up.sh --management-panel`) o abre. Detalhes
da infraestrutura: seção "Infraestrutura local (versão desktop)" do `README.md` da raiz.

## Comandos (em `desktop/`)

| Para | Comando |
|------|---------|
| Rodar em desenvolvimento | `npm run tauri dev` (porta 1420) |
| Testes da interface (vitest + jsdom) | `npm test` |
| Testes do núcleo Rust | `cd src-tauri && cargo test` |
| Verificar vulnerabilidades | `npm audit` e `cargo audit` (em `src-tauri/`) |
| **Gerar o `.app` (comando único)** | `npm run tauri build -- --bundles app --target aarch64-apple-darwin` |
| Gerar o `.app` e verificar a assinatura | `npm run build:app` (o mesmo build, seguido de `npm run verify:app`) |
| Só verificar a assinatura do `.app` já gerado | `npm run verify:app` (`codesign --verify --deep --strict`; sai com erro se a assinatura estiver quebrada) |

Rode `cargo test` antes de `npm test`: ele grava `src-tauri/target/contract-samples.json`, que o
`tests/contract.test.ts` compara com `src/lib/types.ts` (sem o arquivo, esse teste é pulado).

## Build do `.app`

O comando único acima roda `npm run build` (Vite, saída em `desktop/dist/`) e depois o build do Tauri em modo
release, só arm64. O pacote sai em:

```
desktop/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/artigo2md.app
```

(sem `--target`, no mesmo Mac Apple Silicon a saída é `desktop/src-tauri/target/release/bundle/macos/artigo2md.app`).
O build não exige conta de desenvolvedor da Apple: `bundle.macOS.signingIdentity` é `"-"` em
`src-tauri/tauri.conf.json`, a identidade **ad hoc** do `codesign` (sem certificado, sem identidade de
desenvolvedor, sem notarização). Ela sela o bundle inteiro: `codesign -dv` mostra `Signature=adhoc`,
`Info.plist entries=14` e `Sealed Resources version=2`, e `codesign --verify --deep --strict` passa. Sem isso
(só a assinatura ad hoc do linker no executável, `Info.plist=not bound`, `Sealed Resources=none`) o macOS
recusa o `.app` em quarentena como "danificado", sem a opção de abrir mesmo assim. Não há notarização nem `.dmg`
nesta feature.

`npm run build:app` gera o `.app` e roda `npm run verify:app` logo depois; se a assinatura sair quebrada, o
comando termina com erro (contra um `.app` só com a assinatura do linker, a verificação falha com
"code has no resources but signature indicates they must be present").

### Abrir um `.app` sem assinatura (Gatekeeper)

Um `.app` gerado na própria máquina abre direto. Um `.app` recebido por download ou cópia leva o atributo de
quarentena, e o Gatekeeper não abre um app sem assinatura de desenvolvedor identificado na primeira tentativa.
Para liberar:

1. Tente abrir o `artigo2md.app` (duplo clique); o macOS recusa e avisa.
2. Abra **Ajustes do Sistema > Privacidade e Segurança** e use **Abrir Mesmo Assim** para o `artigo2md`.
3. Abra o app de novo e confirme o pedido do macOS.

Esse caminho depende da assinatura ad hoc válida do bundle (item acima): o `.app` anterior, com a assinatura
quebrada, foi recusado pelo macOS como "danificado", sem a opção **Abrir Mesmo Assim** (observação do operador,
tarefa 6.1.4). Com o bundle assinado (cópia assinada com `codesign --force --deep -s -`, equivalente ao build
atual), o operador abriu o app pelo caminho do Gatekeeper (`AppTranslocation` observado). Os nomes exatos dos
botões e a ordem das telas variam com a versão do macOS e não foram reproduzidos por esta documentação; em caso
de divergência vale a documentação da Apple da sua versão.

### SHA-256 do `.app` gerado

Um `.app` é uma pasta; a verificação usa o executável e um manifesto (SHA-256 de cada arquivo, em ordem de
caminho, e depois o SHA-256 dessa lista). Valores do build de 2026-11-26 08:28 (macOS arm64, Rust 1.100.0,
worktree `desktop-shell`, com a correção do Worker de renderização da tarefa 9.2.1 e a assinatura ad hoc do
bundle da tarefa 10.1.1; o hash do executável mudou porque a assinatura ad hoc passa a ser a do bundle):

```
executável  Contents/MacOS/artigo2md-desktop  1f387adc525f0996e30549c9a18c395818bf02ee1b3bfe00c96cc51c5b793922
manifesto   (hash da lista de hashes)          a64ffdcfb7e9fe33950278a71432949133f20048e1620fcd64b8a63a9e8bd1b2
```

Para conferir um `.app` que você recebeu:

```sh
cd artigo2md.app
shasum -a 256 Contents/MacOS/artigo2md-desktop
find . -type f | LC_ALL=C sort | xargs shasum -a 256 | shasum -a 256
```

O build **não é reprodutível bit a bit entre pastas diferentes** (o mesmo código gerado em outra pasta ou sem
`--target` produziu um executável com outro hash). Por isso o hash só vale para o artefato publicado junto
com ele; quem gera o próprio `.app` calcula o próprio hash.

## Licenças das dependências (FR-037, Princípio II)

Relatório por pacote, reproduzível dos lockfiles (detalhes e fontes em `docs/specs/desktop-shell/research.md`
R9.6 e R9.8; registro nominal em `docs/specs/desktop-shell/plan.md`, "Licenças transitivas registradas"):

- **cargo** (`cargo metadata --format-version 1 --filter-platform aarch64-apple-darwin --locked`, em
  `src-tauri/`): 246 pacotes no grafo macOS arm64, **sem GPL/AGPL**. Permissivas MIT/Apache/BSD/Zlib/Unlicense
  (com opção MIT)/0BSD/CC0; 18 crates Unicode-3.0 (família `icu_*`); 5 crates MPL-2.0 (`cssparser`,
  `cssparser-macros`, `dtoa-short`, `option-ext`, `selectors`), transitivos do Tauri e sem modificação,
  aceitos pela constitution 1.2.0.
- **npm** (`package-lock.json`): 166 entradas (35 de produção), **sem GPL/AGPL**; MIT, Apache-2.0, BSD-2/3,
  ISC, 0BSD, BlueOak-1.0.0, PSF-2.0 (`argparse`, via `markdown-it`), `dompurify` usada sob Apache-2.0 e
  `lightningcss` (MPL-2.0, só ferramenta de build, não entra no `.app`).
- **Vulnerabilidades**: `cargo audit` sem vulnerabilidades (3 avisos de pacotes que não entram no build macOS);
  `npm audit` com 2 achados de severidade baixa na cópia aninhada de `katex` dentro de
  `@vscode/markdown-it-katex`, tratados em `research.md` R9.8 (o app usa o `katex` direto, e a cópia afetada não
  vai no pacote).

## Segurança (resumo)

- CSP de produção em `src-tauri/tauri.conf.json` (`app.security.csp`): sem origem remota e sem `'unsafe-inline'`;
  o teste `tests/security-config.test.ts` falha se aparecer origem remota.
- Capabilities (`src-tauri/capabilities/default.json`): só a janela `main`, sem permissão de plugin, de diálogo
  nem de arquivos. Todo acesso a disco passa pelos comandos do lado Rust, confinados à pasta aberta.
- HTML bruto do Markdown é exibido como texto; o HTML renderizado passa por DOMPurify com lista explícita de tags
  e atributos; links externos ficam inativos; imagens remotas nunca são carregadas.
