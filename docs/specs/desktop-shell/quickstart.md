# Quickstart: desktop-shell

Cenários que validam a feature de ponta a ponta. Os comandos rodam em `desktop/`, salvo indicação. Pré-requisitos: Rust 1.90 ou mais novo e Node/npm (plan.md, Pré-requisitos).

Pasta de teste usada nos cenários (`fixtures/sample/`, criada pelos testes ou à mão):

```
sample/
├── .oculto/            # pasta oculta
├── .env                # arquivo oculto
├── Artigos/
│   ├── artigo.md       # tabela GFM, $a^2$, $$\int_0^1 x\,dx$$, ![fig](imagens/figura1.png)
│   ├── quebrado.md     # $$\frac{1}{$$ (fórmula inválida) + texto depois
│   ├── links.md        # [outro](artigo.md), [fora](../../x.md), [site](https://example.org)
│   ├── imagens/figura1.png
│   ├── imagens/desenho.svg
│   └── remoto.md       # ![r](https://example.org/a.png), ![f](../../segredo.png), ![s](imagens/desenho.svg), ![x](imagens/naoexiste.png)
├── binario.md          # bytes que não são UTF-8
├── notas.txt
└── atalho-fora -> /tmp # atalho simbólico para fora
```

Fixtures geradas pelos testes do execute-task (não versionadas à mão): pasta com 5.000 itens (SC-002), ciclo de atalhos (FR-030), subpasta sem permissão de leitura (FR-028) e nomes que diferem só por acento ou caixa (FR-013).

## Scenario 1: Abrir pasta e navegar (US1, happy path)

1. Rodar o app em modo de desenvolvimento com `npm run tauri dev` em `desktop/` (o script `tauri` do `package.json` chama o CLI do Tauri 2; o servidor do Vite usa a porta 1420) ou abrir o `.app` do cenário 8. Antes, `npm ci` em `desktop/`.
2. Conferir a barra lateral com "Arquivos" e "Configurações" e o convite "Abrir pasta…".
3. Clicar em "Abrir pasta…" e escolher `sample/`.
4. **Expected**: árvore com `Artigos/` antes dos arquivos; `binario.md`, `notas.txt` em ordem alfabética sem caixa; `atalho-fora` marcado como atalho e não expansível; `.oculto` e `.env` ausentes; nenhuma ação de editar, renomear ou apagar.
5. Fechar e reabrir o app. **Expected**: a mesma árvore aparece sem nova escolha.

### Protocolo de medição do SC-001 (decisão de desenho, tarefa 2.2.4)

- Participantes: 5 pessoas que nunca usaram o app, sem ajuda nem instrução (o SC-001 diz "uma pessoa"; 5 dá uma amostra mínima, não significância estatística).
- Preparação: `.app` recém-aberto, sem pasta lembrada (configurações apagadas), e a pasta `sample/` visível no Finder. A única tarefa dita é "abra a pasta sample e leia o arquivo artigo.md".
- Medição: cronômetro manual do observador, do primeiro clique da pessoa até o `artigo.md` aparecer renderizado (tabela, fórmulas e imagem visíveis).
- Critério: cada participante conclui em até 30 s; o SC-001 só é considerado atendido se todos os 5 concluírem. Registrar os tempos e qualquer hesitação no relatório do 7.1.5.
- Observação: a medição com pessoas é manual e depende do operador; o execute-task automatizado registra o cenário como "pendente de execução humana" se não houver participantes.

## Scenario 2: Ler Markdown com tabela, fórmulas e imagem (US2, happy path)

1. Com `sample/` aberta, clicar em `Artigos/artigo.md`.
2. **Expected**: tabela com cabeçalho e alinhamento, fórmula em linha e em bloco renderizadas, `figura1.png` visível.
3. Abrir `quebrado.md`. **Expected**: só a fórmula inválida aparece como erro com o texto original; o parágrafo seguinte é renderizado.

## Scenario 3: Imagens e links bloqueados (US2, casos de erro)

1. Abrir `remoto.md`.
2. **Expected**: "Imagem remota bloqueada" no lugar de `https://...`, "Imagem fora da pasta aberta" no lugar de `../../segredo.png`, "Formato de imagem não suportado" no lugar do SVG e "Imagem não encontrada" (com o texto alternativo) no lugar de `naoexiste.png`.
3. Abrir `links.md`, clicar em `outro`. **Expected**: abre `artigo.md`.
4. Clicar em `fora`. **Expected**: "Link fora da pasta aberta"; nada é aberto.
5. Clicar em `site`. **Expected**: nada acontece; o link aparece inativo.

## Scenario 4: Arquivos que não podem ser exibidos (US2, casos de erro)

1. Clicar em `notas.txt`. **Expected**: "Este tipo de arquivo não pode ser visualizado".
2. Clicar em `binario.md`. **Expected**: "Não foi possível ler este arquivo como texto".
3. Abrir `artigo.md`, apagá-lo pelo Finder e acionar "Recarregar". **Expected**: "Arquivo não encontrado".
4. Gerar um `.md` com mais de 5.000.000 bytes e abri-lo. **Expected**: "Arquivo grande: a renderização pode demorar" aparece primeiro; a barra lateral continua respondendo durante a renderização.

## Scenario 5: Configurações (US3)

1. Em "Configurações", escolher aparência escura e o maior tamanho de texto. **Expected**: efeito imediato na janela e no visualizador.
2. Recolher a barra lateral e ajustar a largura antes de recolher; fechar e reabrir. **Expected**: mesmos valores e mesmo estado da barra.
3. Acionar "Restaurar padrões". **Expected**: aparência "seguir o sistema", tamanho médio, barra aberta com a largura padrão; a pasta aberta continua a mesma; após reabrir, os padrões continuam.
4. Com o app fechado, sobrescrever `settings.json` com `{` e abrir o app. **Expected**: o app abre com os padrões e mostra "Não foi possível ler as configurações salvas; os padrões foram restaurados".

## Scenario 6: Pasta anterior indisponível (edge case)

1. Abrir `sample/`, fechar o app, renomear `sample/` no Finder e abrir o app.
2. **Expected**: aviso "A última pasta aberta não está mais disponível" e convite "Abrir pasta…".

## Scenario 7: Zero rede (SC-004)

1. Desligar o Wi-Fi e repetir os cenários 2 e 3. **Expected**: comportamento idêntico.
2. Com a rede ligada, observar o tráfego do processo do app durante uma sessão completa (ferramenta de observação definida no execute-task). **Expected**: nenhuma conexão de saída.

## Scenario 8: Build do `.app` (US4)

1. Em `desktop/`: `npm run tauri build -- --bundles app --target aarch64-apple-darwin` (R5; `--target` fixa o arm64 e muda a pasta de saída; confirmado no build de 2026-11-25). Antes, `cargo test` e `npm test`.
2. O `.app` sai em `desktop/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/artigo2md.app` (sem `--target`, no mesmo Mac: `desktop/src-tauri/target/release/bundle/macos/artigo2md.app`). `lipo -archs` do executável → `arm64`; `Info.plist` → `LSMinimumSystemVersion` `11.0`. Conferir o SHA-256 pelo README de `desktop/`.
3. Abrir o `.app` com a liberação manual do Gatekeeper num Mac Apple Silicon. **Expected**: janela principal com a barra lateral e a árvore vazia.

## Scenario 9: Roundtrip IPC contra o contrato

Não há backend HTTP: a borda é o IPC entre Rust e webview.

1. `cargo test` grava, a partir dos próprios comandos chamados sobre `fixtures/sample/`, os retornos serializados (`list_dir`, `read_markdown`, `get_startup_state`, erro `outsideFolder`) num arquivo JSON temporário de teste.
2. `npm test` executa `contract.test.ts`, que lê esse JSON e confere nomes de campo (camelCase), tipos e valores de enum contra `src/lib/types.ts` e `contracts/commands.md`.
3. **Expected**: nenhum campo ausente, sobrando ou com caixa diferente.

## Scenario 10: Suítes de teste e não regressão

1. Em `desktop/src-tauri`: `cargo test`. Em `desktop/`: `npm test`. Na raiz: `uv run pytest`.
2. **Expected**: as três passam; `git status` não mostra alteração em `src/artigo2md/` nem em `tests/` (FR-036, SC-008).
