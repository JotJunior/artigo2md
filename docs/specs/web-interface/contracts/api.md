# Contracts: web-interface

Contrato da API local da interface web, extraído do código existente (fonte: `src/artigo2md/web.py:120`). Nenhuma rota exige autenticação. Os campos do job estão em [data-model.md](../data-model.md).

## GET /

Devolve a página estática da interface. (fonte: `src/artigo2md/web.py:125`)

## GET /api/info

(fonte: `src/artigo2md/web.py:129`)

### Response (200)

| Field | Type | Description |
|-------|------|-------------|
| version | string | Versão do artigo2md |
| engines | objeto | Motor instalado ou não, por identificador |
| engine_table | lista | Ficha de cada motor |
| choices | lista de string | Valores aceitos para o motor |

## POST /api/jobs

Cria um job por arquivo enviado. (fonte: `src/artigo2md/web.py:134`)

### Request (formulário multipart)

| Field | Type | Required | Validation |
|-------|------|----------|------------|
| files | um ou mais arquivos | sim | Conteúdo que não começa com a assinatura de PDF volta como erro, sem criar job (fonte: `src/artigo2md/web.py:141`) |
| engine | string | não, padrão `auto` | Precisa estar na lista de motores aceitos (fonte: `src/artigo2md/web.py:137`) |

### Response (200)

Lista com um item por arquivo, na ordem do envio: o job criado, ou um objeto com `filename`, `status` igual a `error` e `error` para o arquivo recusado (fonte: `src/artigo2md/web.py:142`).

### Error Responses

| Status | Description |
|--------|-------------|
| 400 | Motor inválido (fonte: `src/artigo2md/web.py:137`) |

## GET /api/jobs

Lista os jobs, do mais recente ao mais antigo. (fonte: `src/artigo2md/web.py:147`)

## GET /api/jobs/{job_id}

Devolve um job. (fonte: `src/artigo2md/web.py:151`)

### Error Responses

| Status | Description |
|--------|-------------|
| 404 | Job não encontrado (fonte: `src/artigo2md/web.py:106`) |

## DELETE /api/jobs/{job_id}

Apaga o job e seus arquivos. (fonte: `src/artigo2md/web.py:155`)

### Response (200)

| Field | Type | Description |
|-------|------|-------------|
| ok | boolean | Sempre verdadeiro quando a remoção acontece |

### Error Responses

| Status | Description |
|--------|-------------|
| 404 | Job não encontrado (fonte: `src/artigo2md/web.py:106`) |
| 409 | Job em fila ou em andamento (fonte: `src/artigo2md/web.py:112`) |

## GET /api/jobs/{job_id}/markdown

Devolve o Markdown de um job concluído. (fonte: `src/artigo2md/web.py:160`)

### Request (query)

| Field | Type | Required | Validation |
|-------|------|----------|------------|
| download | boolean | não, padrão falso | Quando verdadeiro, a resposta vem como arquivo para baixar, com o nome de saída do job |

### Error Responses

| Status | Description |
|--------|-------------|
| 404 | Job não encontrado (fonte: `src/artigo2md/web.py:106`) |
| 409 | Ainda não convertido (fonte: `src/artigo2md/web.py:165`) |

## GET /api/download.zip

Devolve um zip com o Markdown dos jobs concluídos. (fonte: `src/artigo2md/web.py:171`)

### Request (query)

| Field | Type | Required | Validation |
|-------|------|----------|------------|
| ids | string | não | Identificadores separados por vírgula; vazio significa todos os jobs. Jobs inexistentes ou não concluídos são ignorados |

Nomes repetidos dentro do zip recebem um número para ficarem distintos.
