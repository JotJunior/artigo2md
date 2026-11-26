# Data Model: desktop-shell

Não há banco de dados. As entidades abaixo existem em memória (estado do app e da interface) e, no caso das configurações, num único arquivo `settings.json`. Os nomes de campo valem para o payload IPC e para o arquivo (camelCase); nos structs Rust os campos são snake_case, renomeados para camelCase pelo serde (atributo a confirmar; plan.md, Convenções de Borda).

Valores padrão de interface (largura da barra lateral, níveis de texto) são decisões de desenho desta feature, não fatos externos.

## Entity: AppSettings (persistida em `settings.json`)

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| schemaVersion | integer | `= 1` | Versão do formato; outro valor = inválido (FR-023) |
| appearance | enum | `system` \| `light` \| `dark` | Padrão `system` (FR-020) |
| viewerFontSize | enum | `small` \| `medium` \| `large` | Padrão `medium`; três níveis (FR-020) |
| lastFolder | string \| null | caminho absoluto ou `null` | Padrão `null`; validado na inicialização (FR-007, FR-028). Escrito só pelo Rust (`open_folder`, `get_startup_state`); `save_settings` não o aceita |
| sidebarCollapsed | boolean | | Padrão `false` (FR-039) |
| sidebarWidth | integer | 200 ≤ x ≤ 480 (px) | Padrão 260 (FR-039); fora da faixa = inválido |

Regras:

- Leitura: arquivo ausente → padrões, sem aviso. JSON ilegível, campo ausente, tipo errado, enum desconhecido ou valor fora da faixa → padrões com `warning = "settingsReset"` (FR-023). O arquivo inválido não é sobrescrito até a próxima gravação.
- Gravação: arquivo temporário na mesma pasta seguido de rename (escrita atômica). Toda alteração de configuração grava (FR-022); "Restaurar padrões" grava os padrões, preservando `lastFolder` (FR-024 fala das configurações da tela; a última pasta não é controle da tela).
- Local: diretório de configuração do app no macOS, obtido pela API de caminhos do Tauri (a confirmar, research "A verificar").

`UserSettings` (entrada de `save_settings` e retorno de `get_startup_state`, `save_settings` e `reset_settings`): os mesmos campos de `AppSettings`, menos `lastFolder`. O caminho absoluto nunca chega ao webview (plan.md, "Fronteira de confiança").

### State Transitions

```
ausente ──primeira gravação──▶ válido
válido ──corrupção externa──▶ inválido ──leitura──▶ padrões em memória (+aviso) ──gravação──▶ válido
```

## Entity: OpenFolder (estado Rust, não persistido além de `lastFolder`)

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| root | PathBuf | canonizado, existe, é pasta | Guardado em `AppState`; uma só por vez |
| displayName | string | nome da última parte do caminho | Mostrado no topo da árvore |

### State Transitions

```
none ──open_folder ok──▶ open(root) ──open_folder outra──▶ open(root')
open(root) ──raiz some do disco──▶ comandos devolvem folderUnavailable
none ──get_startup_state com lastFolder ausente──▶ none (+aviso lastFolderUnavailable)
```

## Entity: TreeEntry (retorno de `list_dir`)

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| name | string | nome exato do disco, sem normalização | FR-013 |
| relativePath | string | relativo à raiz, separador `/` | Identificador estável do item na interface |
| kind | enum | `directory` \| `file` \| `symlink` | Atalho nunca é expandido (Decision 7) |
| access | enum | `ok` \| `denied` | `denied` mostra "Sem permissão para ler esta pasta" (FR-028) |
| isMarkdown | boolean | extensão `.md` sem distinguir caixa | Arquivo ou atalho para arquivo |
| symlinkTarget | enum \| null | `insideFile` \| `insideDirectory` \| `outside` \| `broken` \| null | Só para `kind = symlink`; `insideFile` + `isMarkdown` pode abrir no visualizador |

Regras (FR-005, FR-008, FR-030):

- Omite nomes iniciados por `.`.
- Ordena pastas antes de arquivos e atalhos; dentro de cada grupo, por nome comparado sem caixa (conversão para minúsculas; função exata a confirmar), com o nome exato como desempate.
- Filhos carregados só quando a pasta é expandida.

### Relationships

- OpenFolder 1:N TreeEntry (por nível, sob demanda).

## Entity: MarkdownDocument (retorno de `read_markdown`)

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| relativePath | string | dentro da raiz | |
| content | string | UTF-8 válido | Caso contrário, erro `notText` (FR-035) |
| sizeBytes | integer | ≥ 0 | |
| isLarge | boolean | `sizeBytes > 5_000_000` | Dispara o aviso do FR-034 (research Decision 3) |

Lido sempre do disco, sem cache: reabrir ou "Recarregar" devolve o conteúdo atual (FR-031).

## Entity: ImageReference (estado da interface, por imagem no documento)

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| src | string | texto original do Markdown | |
| alt | string | | Mostrado nos avisos |
| status | enum | `loading` \| `loaded` \| `remoteBlocked` \| `notFound` \| `outsideFolder` \| `unsupportedFormat` \| `tooLarge` \| `readError` | `readError` cobre `permissionDenied` e `io`; um aviso por status (ver catálogo de mensagens) |
| objectUrl | string \| null | `blob:` | Revogado quando o documento é trocado |

Regras: `src` com esquema (`http:`, `https:`, `data:`, `file:` etc.) ou iniciado por `//` → `remoteBlocked` sem chamada IPC (FR-016). Caminho absoluto → `outsideFolder`. Demais → `read_image`.

## Catálogo de mensagens (FR-003)

Textos literais da spec, chaves em inglês, em `desktop/src/lib/messages.ts`:

| Key | Texto |
|-----|-------|
| navFiles | Arquivos |
| navSettings | Configurações |
| openFolder | Abrir pasta… |
| lastFolderUnavailable | A última pasta aberta não está mais disponível |
| permissionDenied | Sem permissão para ler esta pasta |
| unsupportedFile | Este tipo de arquivo não pode ser visualizado |
| imageNotFound | Imagem não encontrada |
| imageRemoteBlocked | Imagem remota bloqueada |
| imageOutsideFolder | Imagem fora da pasta aberta |
| imageUnsupportedFormat | Formato de imagem não suportado |
| fileNotFound | Arquivo não encontrado |
| linkOutsideFolder | Link fora da pasta aberta |
| largeFile | Arquivo grande: a renderização pode demorar |
| notText | Não foi possível ler este arquivo como texto |
| settingsReset | Não foi possível ler as configurações salvas; os padrões foram restaurados |
| restoreDefaults | Restaurar padrões |
| reload | Recarregar |

### Mensagens definidas no execute-task (tarefas 2.1.1 a 2.1.4, onda-010)

Textos de projeto (não são texto literal da spec; são decisões de desenho desta feature). Todos em português; as chaves seguem em inglês.

**Erros sem texto literal na spec (2.1.1)**

| Key | Código de erro | Texto | Onde aparece |
|-----|----------------|-------|--------------|
| fileTooLarge | `tooLarge` em `read_markdown` | Arquivo grande demais para ser aberto (limite de 50 MB) | Painel do visualizador, no lugar do documento |
| imageTooLarge | `tooLarge` em `read_image` | Imagem grande demais para ser exibida (limite de 20 MB) | No lugar da imagem (status `tooLarge`) |
| invalidSettings | `invalidSettings` | Configuração inválida; nenhuma alteração foi salva | Aviso na tela Configurações |
| ioError | `io` | Não foi possível concluir a operação por um erro de leitura ou gravação | Aviso no painel afetado |
| imageReadError | status `readError` (cobre `permissionDenied` e `io`) | Não foi possível ler a imagem | No lugar da imagem |
| folderUnavailable | `folderUnavailable` ao abrir pasta | A pasta não está disponível | Aviso na tela Arquivos |

**Estados de interface (2.1.2 e 2.1.3)**

| Key | Texto | Onde aparece |
|-----|-------|--------------|
| noFolderOpen | Nenhuma pasta aberta | Área da árvore quando não há raiz; acompanhado do botão `openFolder` ("Abrir pasta…") |
| emptyFolder | Esta pasta não tem arquivos visíveis | Dentro da árvore, quando a pasta aberta (ou uma subpasta expandida) não tem itens visíveis |
| noFileSelected | Selecione um arquivo Markdown na árvore para visualizá-lo | Painel do visualizador enquanto nenhum arquivo está selecionado |
| loadingTree | Carregando… | Sob o nó da pasta enquanto `list_dir` não responde |
| loadingDocument | Carregando documento… | Painel do visualizador entre o clique e a resposta de `read_markdown`/da renderização |
| loadingImage | Carregando imagem… | No lugar da imagem enquanto o status é `loading` |

**Rótulos de controle (2.1.4)**

| Key | Texto |
|-----|-------|
| appearanceLabel | Aparência |
| appearanceSystem | Seguir o sistema |
| appearanceLight | Claro |
| appearanceDark | Escuro |
| fontSizeLabel | Tamanho do texto |
| fontSizeSmall | Pequeno |
| fontSizeMedium | Médio |
| fontSizeLarge | Grande |
| collapseSidebar | Recolher barra lateral |
| expandSidebar | Expandir barra lateral |
| resizeSidebar | Ajustar largura da barra lateral |
| symlinkBadge | Atalho |
| symlinkNotFollowed | Atalho não expandido |
| externalLinkInactive | Links externos estão desativados |
| expandFolder | Expandir pasta |
| collapseFolder | Recolher pasta |

"Seguir o sistema" vem do FR-020 (quickstart, cenário 5: aparência "seguir o sistema").

### Tamanho do texto do visualizador (2.2.1)

Decisão de desenho (valores de projeto, não fatos externos): `small` = 14 px, `medium` = 16 px (padrão), `large` = 19 px, aplicados ao corpo do visualizador (títulos e código escalam em `em` a partir dele).
