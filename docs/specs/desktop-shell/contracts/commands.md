# Contracts: desktop-shell — comandos Tauri (IPC)

> **[PROPOSTA — a validar na implementação]** Contrato novo, desenhado nesta feature; não descreve nenhuma API existente. A mecânica (`#[tauri::command]`, `generate_handler!`, `invoke` de `@tauri-apps/api/core`, erros serializáveis, `tauri::ipc::Response`) vem de https://v2.tauri.app/develop/calling-rust/ (research R6).

Regras gerais:

- Todos os comandos são `async` no Rust (rodam fora da thread principal, R6).
- Argumentos e retornos em camelCase; nomes de comando em snake_case.
- Caminhos trocados com a interface são sempre **relativos à pasta aberta**, com separador `/`. **Nenhum caminho absoluto vem da interface**: a pasta é escolhida pelo diálogo nativo aberto pelo lado Rust (`open_folder` sem argumentos) e `lastFolder` só é escrito pelo Rust (gate owasp-security H1, onda-006).
- Todo caminho recebido passa por `paths.rs`, nesta ordem: (1) rejeita caminho absoluto; (2) junta com a raiz (ou com a pasta do `.md`) e resolve `.`/`..` por componentes, **sem tocar o disco**; se sair da raiz, devolve `outsideFolder` (assim um destino inexistente fora da raiz também dá `outsideFolder`, nunca `notFound`, e o app não revela o que existe fora da pasta); (3) só então canoniza (resolve atalhos) e confere de novo, por componente e não por prefixo de texto, que o resultado está dentro da raiz canonizada. A janela entre canonizar e abrir (atalho trocado no meio) é risco aceito nesta feature: o app é somente leitura e a pasta é do próprio usuário.
- Nenhum comando escreve no disco do usuário. Só `open_folder` (campo `lastFolder`), `save_settings` e `reset_settings` escrevem, e apenas no `settings.json` do app; as três gravações passam pelo mesmo `Mutex` no Rust, sem corrida entre elas.
- A interface só chama comandos por `desktop/src/lib/api.ts`.

## Erros

Rejeição do `invoke` com o objeto:

| Field | Type | Description |
|-------|------|-------------|
| code | ErrorCode | Código estável, mapeado para texto em `messages.ts` |
| detail | null | Sempre `null` na interface: o texto técnico (que pode conter caminho absoluto ou mensagem do sistema) é registrado só no log do lado Rust (gate owasp-security L2) |

`ErrorCode`:

| Code | Quando | Mensagem (messages.ts) |
|------|--------|------------------------|
| noFolderOpen | comando de leitura sem pasta aberta | (estado vazio, convite "Abrir pasta…") |
| folderUnavailable | raiz ausente, desmontada ou não é pasta | lastFolderUnavailable |
| permissionDenied | sem permissão de leitura | permissionDenied |
| notFound | arquivo ou pasta inexistente | fileNotFound / imageNotFound |
| outsideFolder | caminho resolvido fora da raiz | imageOutsideFolder / linkOutsideFolder |
| notMarkdown | arquivo que não é `.md` | unsupportedFile |
| notText | conteúdo não é UTF-8 válido | notText |
| unsupportedFormat | imagem fora de PNG/JPEG/GIF/WebP por extensão ou assinatura | imageUnsupportedFormat |
| tooLarge | arquivo acima do limite rígido de leitura: `.md` > 50.000.000 bytes ou imagem > 20.000.000 bytes (plan.md, Endurecimento de segurança; decisão do operador, block-006) | fileTooLarge (`.md`) / imageTooLarge (imagem) |
| invalidSettings | `save_settings` recebeu valor fora do esquema | invalidSettings |
| io | outra falha de E/S | ioError (imagem: imageReadError) |

Regra de leitura (plan.md, "Leitura limitada ao teto"): `read_markdown` e `read_image` conferem o tamanho pelos metadados **e** leem com limite (`Read::take(limite + 1)`); se o conteúdo lido passar do limite, devolvem `tooLarge`, mesmo que os metadados tenham informado um tamanho menor (arquivo que cresceu entre a conferência e a leitura).

## get_startup_state

Chamado uma vez quando a janela abre.

### Request

Sem argumentos.

### Response

| Field | Type | Description |
|-------|------|-------------|
| settings | UserSettings | Configurações válidas ou padrões, sem `lastFolder` (data-model.md) |
| warnings | string[] | Subconjunto de `settingsReset`, `lastFolderUnavailable` |
| folder | OpenFolderInfo \| null | Pasta reaberta a partir de `lastFolder`, se ainda existir (FR-007) |

`OpenFolderInfo`: `{ displayName: string }`.

Efeito: se `lastFolder` existe e é pasta legível, vira a raiz do `AppState`. Se não, a raiz fica vazia, `lastFolder` passa a `null` na próxima gravação e `warnings` inclui `lastFolderUnavailable` (FR-028).

## open_folder

### Request

Sem argumentos. O lado Rust abre o diálogo nativo de escolha de pasta (FR-004) pela API Rust do `tauri-plugin-dialog` e usa só o caminho que o diálogo devolveu. O nome exato dessa API está em research "A verificar"; se ela não existir, o execute-task para com bloqueio humano, sem voltar a aceitar caminho vindo da interface.

### Response

| Field | Type | Description |
|-------|------|-------------|
| folder | OpenFolderInfo \| null | `null` se a pessoa cancelou o diálogo |

Efeito: guarda a raiz canonizada no `AppState` e grava `lastFolder` (FR-007, FR-022). Erros: `folderUnavailable`, `permissionDenied`.

## list_dir

### Request

| Field | Type | Required | Validation |
|-------|------|----------|------------|
| relativePath | string | yes | `""` para a raiz; confinado |

### Response

| Field | Type | Description |
|-------|------|-------------|
| entries | TreeEntry[] | Um nível, já filtrado e ordenado (data-model.md) |

Erros: `noFolderOpen`, `notFound`, `permissionDenied` (da pasta pedida), `outsideFolder`. Subpasta sem permissão dentro do nível listado volta como entrada com `access = "denied"`, sem erro.

## read_markdown

### Request

| Field | Type | Required | Validation |
|-------|------|----------|------------|
| relativePath | string | yes | confinado; extensão `.md` sem distinguir caixa |

### Response

`MarkdownDocument` (data-model.md): `{ relativePath, content, sizeBytes, isLarge }`.

Erros: `noFolderOpen`, `notFound`, `permissionDenied`, `outsideFolder`, `notMarkdown`, `notText`, `tooLarge` (acima de 50.000.000 bytes, conferido pelos metadados antes da leitura).

## read_image

### Request

| Field | Type | Required | Validation |
|-------|------|----------|------------|
| documentPath | string | yes | caminho relativo do `.md` que referencia a imagem |
| src | string | yes | texto original da referência, relativo à pasta do `.md`, sem esquema |

### Response

Bytes do arquivo via `tauri::ipc::Response` (R6), até o limite rígido de 20.000.000 bytes por imagem (plan.md, Endurecimento de segurança; acima dele, erro `tooLarge`, conferido pelos metadados antes da leitura). O tipo recebido no JS não está documentado na fonte lida; confirmar no execute-task. Se não for `ArrayBuffer`, usar o plano B da Decision 5 (`number[]`) e registrar a mudança neste contrato. A interface monta `Blob` + `URL.createObjectURL`.

Validação, em ordem:

1. Confinamento → `outsideFolder` (FR-032).
2. Existência → `notFound` (FR-014).
3. Extensão em `png`, `jpg`, `jpeg`, `gif`, `webp` (sem distinguir caixa) → senão `unsupportedFormat`.
4. Assinatura dos primeiros bytes compatível com a extensão (research R8) → senão `unsupportedFormat` (FR-041).

Outros erros: `noFolderOpen`, `permissionDenied`, `io`.

O `src` pode chegar do markdown-it com codificação percentual; se isso se confirmar (research "A verificar"), a interface decodifica antes de chamar `read_image`, e um teste com nome acentuado cobre o caso.

O nome é resolvido exatamente como está no disco, sem normalização Unicode nem de caixa (FR-013). Não há fonte nesta data sobre o sistema de arquivos do macOS aceitar um nome com outra caixa (research "A verificar"); por isso a checagem do nome exato compara o componente final com a listagem da pasta, e o teste desse caso fica no execute-task.

## resolve_link

### Request

| Field | Type | Required | Validation |
|-------|------|----------|------------|
| documentPath | string | yes | `.md` de origem |
| href | string | yes | relativo, sem esquema |

### Response

| Field | Type | Description |
|-------|------|-------------|
| relativePath | string | Destino confinado, pronto para `read_markdown` |

Erros: `outsideFolder` (mensagem linkOutsideFolder), `notFound` (fileNotFound), `notMarkdown`. Fragmento (`#...`) e querystring do `href` são descartados antes da resolução.

## save_settings

### Request

| Field | Type | Required | Validation |
|-------|------|----------|------------|
| settings | UserSettings | yes | `AppSettings` sem `lastFolder` (data-model.md); inválido → erro `invalidSettings`. `lastFolder` nunca vem da interface |

### Response

`UserSettings` gravadas (sem `lastFolder`; o Rust preserva o valor no arquivo). Escrita atômica (data-model.md).

Implementação (onda-010): o comando recebe `settings` como JSON cru (`serde_json::Value`) e valida no Rust, para devolver sempre `invalidSettings` (e não o erro genérico de deserialização do Tauri) quando há campo desconhecido (inclui `lastFolder`), enum fora do domínio, tipo errado ou `sidebarWidth` fora de 200 a 480. Na interface, `api.ts` continua tipando o argumento como `UserSettings`.

Fronteira de confiança (converge, tarefa 8.1.1): `get_startup_state`, `save_settings` e `reset_settings` devolvem `UserSettings`, nunca `AppSettings`. O `lastFolder` (caminho absoluto) existe só no Rust e no `settings.json`; a interface o conhece apenas pelo aviso `lastFolderUnavailable` e pelo `displayName` da pasta aberta.

## reset_settings

### Request

Sem argumentos.

### Response

`UserSettings` com os padrões gravados (FR-024); o Rust preserva `lastFolder` no arquivo, sem devolvê-lo à interface.

## Permissões (capabilities)

Arquivo `desktop/src-tauri/capabilities/default.json`, só para a janela principal (`"windows": ["main"]`, sem `remote`). Lista de permissões do core e de plugins: **vazia** (`"permissions": []`). Motivos:

- A interface só chama os comandos do app acima por `invoke` (`lib/api.ts`). Pelo código do Tauri 2.12.7 (`webview/mod.rs`, bloco "Check ACL on plugin commands, when the app defined its ACL manifest, or when the request comes from a non-local (remote) origin"), comando próprio do app chamado de origem local não passa por permissão quando o app não define manifesto de ACL; só comandos de plugin (`plugin:...`) e origens remotas passam.
- Sem `dialog:allow-open` para o webview: o diálogo de pasta é aberto pelo Rust (`open_folder`). Sem permissão de arquivos (`fs:*`) para o webview: todo acesso passa pelos comandos acima. Sem `core:default` nem outro conjunto padrão.
- O teste `desktop/tests/security-config.test.ts` falha se a capability deixar de ser só da janela `main`, ganhar `dialog:allow-open`, permissão `fs:*`/`dialog:*`/`shell:*`/`http:*` ou um conjunto `:default`.
