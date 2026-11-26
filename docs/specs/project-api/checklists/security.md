# Security Checklist: API da versão desktop (project-api)

**Purpose**: validar a qualidade dos requisitos de segurança da `project-api` (exposição local, origem de navegador, confinamento de caminhos, entrada não confiável vinda da fila, limites de recurso, mensagens de erro, isolamento do container, licenças). Testa o que está ESCRITO na spec/plan/research/contracts, não a implementação.
**Created**: 2026-11-25
**Feature**: [spec.md](../spec.md)
**Profundidade / audiência**: Standard; reviewer do PR (defaults da skill — execução autônoma feature-00c, sem operador interativo) [Assumption]

## Exposição e acesso

- [x] CHK001 - O requisito de aceitar conexões só em `127.0.0.1` e de recusar `Host` diferente de `127.0.0.1`/`localhost` é inequívoco e verificável? [Clareza, Spec §FR-027, US6-1, US6-2; plan.md "Constitution Check" I; contracts/configuration.md "Mantidos sem mudança"] {auto}
  - Evidência: porta `127.0.0.1:${ARTIGO2MD_API_PORT}:8000` mantida; `Host` recusado como na API base (`TrustedHostMiddleware`, research Decision 9); cenário 7 passos 1-2 do quickstart.
- [x] CHK002 - A decisão de não ter autenticação está registrada com seu risco residual, alternativa rejeitada e fundamento no escopo monousuário? [Premissa, Spec §US6 (introdução); research.md Decision 9 "Alternatives considered"; plan.md S-08, R-03] {auto}
  - Evidência: "sem login (app monousuário e local)" na spec; token de sessão local rejeitado em D9; risco "qualquer processo local chama a API" aceito e documentado no README (R-03).
- [x] CHK003 - A origem de produção do app desktop (`tauri://localhost`, fixa no código desde a remoção de `ARTIGO2MD_API_ALLOWED_ORIGINS`, dec-052) está confirmada por fonte, e não apenas adotada de plano de outra feature "a confirmar"? [Assumption, Spec §FR-028; research.md F19 e Decision 9; plan.md R-01; contracts/configuration.md] -> Decisao do operador (block-001/dec-023, 2026-11-25): origem de producao confirmada como `tauri://localhost`; fonte citada pelo operador: tauri 2.12.7 (Cargo.lock da desktop-shell), `src/manager/mod.rs` `get_app_url` (no macOS com `frontendDist` em caminho retorna `tauri://localhost`); `desktop/src-tauri/tauri.conf.json` da desktop-shell usa `frontendDist ../dist` e `devUrl http://localhost:1420`. Liberar so `tauri://localhost` por padrao; a origem de desenvolvimento `http://localhost:1420` somente por variavel de ambiente explicita, desligada por padrao (requisito novo: ver spec FR-028, plan R-01/S-01, contracts/configuration.md).
  - Aberto: F19 cita `desktop-shell/plan.md:240` ("A origem exata de produção no macOS é confirmada no cenário 8 do quickstart" daquela feature, ainda não integrada a `main`). Se a origem real diferir, muda a constante no código (não há mais variável, dec-052); confirmar é decisão/ação do dono (feature `desktop-shell`/`desktop-projects`). Nenhum item aqui auto-resolve isso.
- [x] CHK004 - A política de `Origin` (recusa de qualquer `Origin` fora da lista, inclusive `null`; aceite de pedido sem `Origin`) cobre todas as rotas de negócio e o SSE antes de qualquer efeito? [Completude, Spec §FR-028, US6-3; contracts/http-api.md "Origem"; contracts/live-events.md] {auto}
  - Evidência: 403 `origin_not_allowed` "antes de qualquer efeito"; "Mesmas regras de `Host` e `Origin` das demais rotas" no SSE; rotas sem corpo (`archive`, `cancel`, `DELETE`) ficam cobertas por serem rotas de negócio.
- [x] CHK005 - A configuração de origens falha de forma segura e não admite `*`? [Cobertura, contracts/configuration.md "Lista de origens fixa"] {auto}
  - Evidência: a lista é fixa no código (`tauri://localhost`, mais a de desenvolvimento só com `ARTIGO2MD_API_ALLOW_DEV_ORIGIN`); não há variável que a troque, logo nenhum valor de ambiente libera `*` nem outra origem. Valor inválido em `ARTIGO2MD_API_ALLOW_DEV_ORIGIN` → permanece desligada, aviso em português.
  - Histórico: o item foi escrito para `ARTIGO2MD_API_ALLOWED_ORIGINS` (valor inválido → lista vazia; `*` recusado); a variável foi removida por decisão do operador (tarefa 13.9, dec-052).
- [x] CHK006 - Os métodos e cabeçalhos permitidos pelo CORS (preflight de `PATCH`, `DELETE`, `Content-Type: application/json`) estão enumerados no contrato? [Gap, research.md Decision 9 ("métodos e cabeçalhos explícitos"); contracts/http-api.md "Regras gerais"] -> Fechado na onda-006 (tarefa 2.x): contracts/http-api.md "CORS": metodos GET/POST/PATCH/DELETE/OPTIONS, cabecalho Content-Type, expose Content-Disposition, sem credenciais.
  - Gap: a Decision 9 diz "explícitos" mas nenhum documento lista quais; o cliente do app precisa deles para o preflight. Destino: `/create-tasks`.
- [x] CHK007 - A defesa contra envio entre origens sem preflight (CSRF por formulário) tem requisito próprio, além do CORS? [Cobertura, research.md Decision 9; contracts/http-api.md 415] {auto}
  - Evidência: `Content-Type: application/json` obrigatório nas rotas com corpo (415) e recusa por `Origin` no servidor.

## Confinamento de caminhos e arquivos

- [x] CHK008 - A regra única host→container e de confinamento está especificada de forma testável (componente `..`/`.`, nulo, não absoluto, fronteira de componente, `O_NOFOLLOW`, `fstat` regular, `realpath` sob o ponto de montagem)? [Clareza, Spec §FR-025, SC-003; research.md Decision 10] {auto}
  - Evidência: `to_container_path` em `api/paths.py` e abertura confinada descritas na Decision 10; bateria de caminhos maliciosos em `test_api_paths.py` e cenário 7 passo 3.
- [x] CHK009 - Os textos da Decision 10 (confere `realpath` do arquivo) e do achado S-07 (abrir com `O_NOFOLLOW` e conferir o caminho real do descritor via `/proc/self/fd`) descrevem a mesma sequência de verificação? [Ambiguity, research.md Decision 10 vs plan.md S-07] -> Fechado na onda-006 (tarefa 2.x): research.md Decision 10 reescrita na mesma sequencia de plan.md S-07.
  - Gap: S-07 refina D10 (corrida CWE-367), mas D10 não foi atualizada; o reviewer não sabe qual é a regra vigente. Destino: `/clarify` (atualizar D10).
- [x] CHK010 - Existe requisito para a verificação do descritor onde `/proc/self/fd` não existe (testes `uv run pytest` no macOS, sem Docker)? [Gap, plan.md S-07 ("no container Linux"); Spec §FR-034; Spec §SC-003] -> Fechado na onda-006 (tarefa 2.x): research.md Decision 10 e plan S-07: fcntl.F_GETPATH + (st_dev, st_ino) onde nao ha /proc; sem mecanismo -> recusa.
  - Gap: o plan fixa o mecanismo só para o container Linux, mas FR-034/SC-003 exigem a bateria de caminhos maliciosos passando em `uv run pytest` sem Docker, no ambiente do desenvolvedor. Destino: `/create-tasks` (especificar o equivalente portável).
- [x] CHK011 - O caminho vindo do evento `result` (`markdown_path`) é validado antes de ser persistido, e o arquivo fora do diretório de dados nunca é lido nem entregue? [Cobertura, Spec §US4-6, FR-025; data-model.md "State transitions" e "Mapeamento evento da fila → gravação"] {auto}
  - Evidência: "caminho de MD fora do data dir: fail_job"; quickstart cenário 7 passo 3 (`..`, fora do diretório, link simbólico) com `GET .../markdown` recusado. O estado final do job é discutido em api.md CHK016 (ambiguidade de redação, não de segurança).
- [x] CHK012 - A exclusão de projeto remove apenas caminhos derivados pela API, nunca caminhos informados pelo cliente? [Completude, Spec §FR-004; plan.md S-01; contracts/http-api.md "DELETE"] {auto}
  - Evidência: só `<mount>/<job_id>/` e `<mount>/sources/<project_id>/`, com `lstat`, `realpath` e `shutil.rmtree`; arquivos de fontes in_place nunca tocados.
- [x] CHK013 - Os nomes de entrada do zip de exportação derivam só do nome de arquivo (sem diretórios) do Markdown já confinado, com regra de colisão? [Clareza, Spec §FR-021; research.md Decision 11] {auto}
  - Evidência: `<stem>.md` / `<stem>-<document_id>.md`, `<stem>` = nome do arquivo sem diretórios; arquivo que falhar na leitura confinada fica fora do zip e listado em `LEIA-ME.txt`.
- [x] CHK014 - O risco de o cliente local registrar qualquer caminho `.pdf` absoluto legível (o worker o lerá) está reconhecido como aceito e atribuído ao componente certo? [Premissa, plan.md S-10; conversion-queue/plan.md P-02] {auto}
  - Evidência: P-02 (quem tem credenciais do broker é confiável; sem allowlist de pastas) e S-10 ("a API não amplia: só repassa o caminho registrado").

## Entrada não confiável da fila

- [x] CHK015 - O consumo de evento tem limites de tamanho e profundidade aplicados antes do parse e descarte seguro de eventos fora do contrato? [Completude, Spec §FR-016; plan.md S-02; conversion-queue/contracts/queue-messages.md "Limites de entrada"] {auto}
  - Evidência: `consumer.py` aplica `MAX_BODY_BYTES` e `json_depth <= MAX_JSON_DEPTH` antes de `validate_event`; acima → descarte com log em português; evento inválido não derruba o consumo (US2-8).
- [x] CHK016 - O texto livre dos eventos (`reason`, `issues`, `message`) tem limite antes de ir ao banco e ao SSE? [Cobertura, plan.md S-06] {auto}
  - Evidência: truncamento em 2000 caracteres por campo [valor PROPOSTA; aceitação no api.md CHK031].
- [x] CHK017 - A fronteira de confiança do broker está declarada: quem pode publicar em `artigo2md.events` e o que impede um publicador local de forjar um `result`/`error` bem formado para um `job_id` real? [Gap, Spec §FR-013, FR-016; plan.md S-10; conversion-queue/plan.md P-02] -> Decisao do operador (block-001/dec-023, 2026-11-25): risco aceito (credenciais do broker = confianca total; mesma premissa P-02 da conversion-queue).
  - Aberto: P-02 trata o pedido ao worker, não a falsificação de eventos para a API; o quickstart cenário 5 até publica eventos à mão. Aceitar o risco (credenciais do broker = confiança total) é decisão do dono e deve ficar escrita no plan.
- [x] CHK018 - A idempotência do consumo (job, tentativa) e o tratamento de job final, inexistente, de projeto arquivado/excluído e de `schema_version` desconhecida estão especificados sem depender de sorte do broker? [Cobertura de cenários, Spec §FR-016, US2-7, US2-8, Edge Cases] {auto}
  - Evidência: data-model.md "Mapeamento evento da fila → gravação" e transição final → "sem mudança (InvalidJobTransitionError = duplicata, descartado)".

## Limites de recurso (disponibilidade)

- [x] CHK019 - Cada recurso limitado tem comportamento definido ao exceder: corpo (413), assinantes SSE (503 `too_many_streams`), exportação (409/413), lote com broker fora (falha imediata sem nova espera)? [Cobertura, contracts/http-api.md "Corpo de erro"; plan.md S-03, S-04, S-05] {auto}
  - Evidência: tabela de códigos de erro e achados S-03 a S-05 do plan.
- [x] CHK020 - A ausência de limitação de taxa (por exemplo, repetição de `POST .../jobs` ou de abertura de SSE por um processo local) está declarada como decisão explícita? [Gap, Spec §"Decisões de infraestrutura" (nota após FR-034); plan.md S-03, S-08] -> Decisao do operador (block-001/dec-023, 2026-11-25): ausencia de limitacao de taxa aceita como decisao explicita (monousuario local).
  - Aberto: a nota da spec lista scheduling, chaves, token, mutex e backup como N/A, mas não rate limiting; os tetos S-03/S-04/S-05 mitigam, não declaram a decisão. Aceitar "N/A por ser monousuário local" é decisão do dono.

## Erros, logs e segredos

- [x] CHK021 - O requisito de mensagens em português sem senha, credencial, texto bruto de driver ou pilha é especificado para resposta e log, com mensagem fixa para erro interno? [Clareza, Spec §FR-031, SC-002; contracts/http-api.md "Corpo de erro" e parágrafo final; plan.md Constitution III] {auto}
  - Evidência: `500 internal_error` com "mensagem fixa em português, sem detalhe interno"; `invalid_request` sem eco do valor; erros do store já em português (`repository.md`).
- [x] CHK022 - O tratamento de credenciais da configuração (`RABBITMQ_DEFAULT_PASS`, `POSTGRES_PASSWORD`) evita vazamento em log, `repr` e mensagem de falha de configuração? [Cobertura, contracts/configuration.md linhas e parágrafo final; data-model.md "Entity: ApiSettings"] {auto}
  - Evidência: "mensagem cita só o nome da variável", "Valores de senha nunca aparecem em log nem em resposta", `credentials` com `repr` sem a senha.
- [x] CHK023 - O risco de credenciais no `environment` do container (`docker inspect`) está reconhecido e vinculado ao que a stack já aceita? [Premissa, plan.md S-09; stack/.env.example] {auto}

## Isolamento do container e do processo

- [x] CHK024 - O requisito de que o container receba só o diretório de dados e mantenha as proteções atuais (usuário `10001`, `read_only`, `cap_drop: ALL`, `no-new-privileges`, `tmpfs /tmp`) é explícito e verificável? [Completude, Spec §FR-030, US6-5; contracts/configuration.md "stack/compose.yaml"; quickstart.md cenário 7 passo 4] {auto}
  - Evidência: "único volume do serviço" + lista "Mantidos sem mudança"; verificação por `docker inspect` (`Mounts`).
- [x] CHK025 - A proibição de carregar motores de conversão tem critério verificável e escopo igual entre spec e plan? [Mensurabilidade, Spec §FR-029, US6-4, SC-007; quickstart.md cenário 7 passo 5; plan.md "Constraints"] {auto}
  - Evidência: `sys.modules` em processo novo sem `docling`, `pymupdf4llm`; o plan/quickstart ampliam a verificação para `pypdfium2` e `artigo2md.router` (mais estrito que a spec, sem contradição).
- [x] CHK026 - A pasta de dados montada tem o modo de acesso compatível com os requisitos (escrita necessária para remover arquivos na exclusão, FR-004)? [Consistência, Spec §FR-004, FR-030; contracts/configuration.md linha `volumes` (`:/data`, sem `:ro`)] {auto}
  - Evidência: o mapeamento é leitura e escrita; `read_only: true` vale só para o sistema de arquivos raiz do container, e a remoção fica confinada por S-01.

## Privacidade e licenças

- [x] CHK027 - A promessa de processamento 100% local está refletida: nenhum serviço externo, nenhuma telemetria, e o conteúdo do PDF nunca passa pela API? [Completude, Spec §FR-006; plan.md "Constitution Check" I; constitution Princípio I] {auto}
  - Evidência: "API fala só com `postgres`/`rabbitmq` da rede do compose"; filtro de endereço loopback/privado (`ARTIGO2MD_RABBITMQ_HOST`); Q1 do clarify.
- [x] CHK028 - A única mudança de dependência (`pika==1.5.0`) tem licença permissiva verificada em fonte? [Dependência, plan.md "Constitution Check" II; research.md F16; contracts/configuration.md "pyproject.toml"] {auto}
  - Evidência: BSD-3-Clause, verificada pelo operador em `conversion-queue/research.md` L15/L16.

## Notes

- Items `{auto}` ja vem resolvidos pelo agente (`[x]` com citacao, ou marcador `[Gap]`/`[Ambiguity]` mantendo `[ ]`)
- Items `{humano}` decididos pelo dono do produto em 2026-11-25 (block-001/dec-023) e marcados `[x]` com a decisao citada em cada item
- Destinos: `[Ambiguity]` (CHK009) → `/clarify`; `[Gap]` (CHK006, CHK010) → `/create-tasks`; `{humano}` → decisao antes de `/execute-task`
- Sem achados `critical`/`high` abertos nos requisitos; o gate `owasp-security` do plan (S-01 a S-10) foi incorporado e conferido nos itens acima
