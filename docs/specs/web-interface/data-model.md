# Data Model: web-interface

## Entity: Job

Registro de uma conversão pedida pela interface web, gravado como arquivo JSON na pasta do job dentro do diretório de dados. (fonte: `src/artigo2md/web.py:50`)

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| id | string | 12 caracteres hexadecimais; identifica o job e nomeia sua pasta | Gerado na criação (fonte: `src/artigo2md/web.py:63`) |
| filename | string | Nome do arquivo enviado | (fonte: `src/artigo2md/web.py:68`) |
| stem | string | Nome de saída sanitizado, até 150 caracteres | (fonte: `src/artigo2md/web.py:25`) |
| size | integer | Tamanho do PDF em bytes | (fonte: `src/artigo2md/web.py:69`) |
| engine_requested | string | Um de: auto, docling, pymupdf, mineru, markitdown | Validado no envio (fonte: `src/artigo2md/web.py:136`) |
| status | enum | queued, running, done, error | Valor inicial: queued (fonte: `src/artigo2md/web.py:69`) |
| stage | string | Texto da etapa atual | Valor inicial "Na fila"; atualizado pelo progresso (fonte: `src/artigo2md/web.py:70`) |
| created_at | number | Instante de criação em segundos | Usado na ordenação da lista (fonte: `src/artigo2md/web.py:70`) |
| started_at | number | Presente a partir do início da conversão | (fonte: `src/artigo2md/web.py:85`) |
| finished_at | number | Presente ao concluir ou falhar | (fonte: `src/artigo2md/web.py:97`) |
| engine | string | Motor que produziu o resultado; presente quando done | (fonte: `src/artigo2md/web.py:96`) |
| analysis | object | Resumo da inspeção do PDF; presente quando done | (fonte: `src/artigo2md/web.py:96`) |
| attempts | array | Tentativas por motor; presente quando done | (fonte: `src/artigo2md/web.py:97`) |
| quality | object | Qualidade da última tentativa avaliada; pode ser nulo | (fonte: `src/artigo2md/web.py:93`) |
| error | string | Mensagem de erro, até 800 caracteres; presente quando error | (fonte: `src/artigo2md/web.py:100`) |

### Relationships

- Job 1:1 PDF de origem, gravado como source.pdf na pasta do job (fonte: `src/artigo2md/web.py:66`)
- Job 1:1 Markdown resultante, gravado como result.md na pasta do job quando a conversão conclui (fonte: `src/artigo2md/web.py:91`)
- Job 1:1 metadados, gravados como job.json na pasta do job (fonte: `src/artigo2md/web.py:54`)

### State Transitions (if applicable)

```
queued -> running -> done
queued -> running -> error
queued | running -> error   (servidor reiniciado)
```

- Transições de execução (fonte: `src/artigo2md/web.py:78`)
- Jobs em fila ou em andamento viram error ao recarregar após reinício (fonte: `src/artigo2md/web.py:46`)
- Só jobs done ou error podem ser apagados (fonte: `src/artigo2md/web.py:111`)
