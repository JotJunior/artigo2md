# Research: conversion-engines

Documento produzido no Phase 0 do `/plan`. Resolve os unknowns do Technical Context antes do design.
Escopo do plano: atender FR-013 (nenhum motor envia conteúdo a serviços externos; telemetria desativada
quando o motor oferece a opção) e criar os testes que faltam para FR-002, FR-005 a FR-009, FR-012 e
FR-013, exigidos por SC-001.

As observações abaixo foram feitas em 2026-11-20 lendo os pacotes instalados pelo `uv sync` no
ambiente do projeto. Valem para as versões citadas; outras versões precisam ser reverificadas.

## Decision 1: Docling — serviços remotos

**Decision**: `[PROPOSTA — a validar na implementacao]` declarar explicitamente, nas opções de pipeline que o adaptador monta, que serviços remotos ficam desligados, em vez de depender do valor padrão.
**Rationale**: No docling 2.151.1, a opção `enable_remote_services` das opções de pipeline tem padrão falso e a descrição diz que ela permite chamar APIs externas durante o processamento (arquivo `docling/datamodel/pipeline_options.py` do pacote). O adaptador cria as opções sem tocar nesse campo (fonte: `src/artigo2md/engines.py:53`), então hoje vale o padrão. Fixar o valor protege contra mudança de padrão em versão futura, risco registrado no briefing.
**Alternatives considered**: Manter o padrão implícito (fica sem garantia quando o Docling mudar); fixar a versão do Docling (o `pyproject.toml` aceita a partir da 2.40 e não há pedido para restringir).

## Decision 2: Docling — download de modelos e telemetria do Hugging Face Hub

**Decision**: `[PROPOSTA — a validar na implementacao]` definir a variável de ambiente `HF_HUB_DISABLE_TELEMETRY` no processo antes de qualquer import do Docling; manter o download de modelos na primeira conversão como está.
**Rationale**: O docling 2.151.1 baixa modelos com `snapshot_download` do huggingface_hub (arquivo `docling/models/utils/hf_model_download.py` do pacote). O huggingface_hub 1.33.0 lê `HF_HUB_DISABLE_TELEMETRY` (ou `DISABLE_TELEMETRY`) e `HF_HUB_OFFLINE` no carregamento do módulo de constantes (arquivo `huggingface_hub/constants.py` do pacote), por isso a variável precisa estar definida antes do import. O download de modelos traz dados para a máquina e não envia o documento; o README já o descreve (fonte: `README.md:34`).
**Alternatives considered**: Forçar modo offline com `HF_HUB_OFFLINE` (quebraria a primeira conversão, que precisa baixar os modelos); não tratar a telemetria do hub (FR-013 pede desativar quando há a opção).

**Pergunta em aberto para o operador**: FR-013 fala de envio de conteúdo e de telemetria. O download de modelos na primeira execução é acesso à rede que não se encaixa em nenhum dos dois; este plano o mantém. Se a intenção for proibir também esse acesso depois de os modelos estarem em cache, isso é requisito novo.

## Decision 3: MarkItDown — conversores em nuvem

**Decision**: Manter a chamada atual, sem endpoint de nuvem, e cobrir com teste.
**Rationale**: No markitdown 0.1.9, os conversores Document Intelligence e Content Understanding só são registrados quando o chamador passa o respectivo endpoint (arquivo `markitdown/_markitdown.py` do pacote). O adaptador instancia o MarkItDown só com plugins desligados, sem endpoint nem cliente de LLM (fonte: `src/artigo2md/engines.py:93`).
**Alternatives considered**: Nenhuma mudança de código é necessária; a alternativa seria não testar, o que descumpre SC-001.

## Decision 4: pymupdf4llm — acesso à rede

**Decision**: Sem mudança de código; cobrir com teste em tempo de execução.
**Rationale**: No pymupdf4llm 2.1 não foi encontrado import de `requests`, `httpx`, `urllib.request` nem `socket` nos arquivos do pacote. A busca não cobriu o pymupdf, do qual ele depende, por isso a garantia vem do teste com sockets bloqueados (Decision 6), não desta leitura.
**Alternatives considered**: Auditar todo o pymupdf estaticamente (caro e ainda sujeito a mudança de versão).

## Decision 5: MinerU — telemetria

**Decision**: Sem mudança de código; cobrir com teste.
**Rationale**: O adaptador já executa o MinerU com a variável `MINERU_TELEMETRY_DISABLED` definida (fonte: `src/artigo2md/engines.py:160`).
**Alternatives considered**: —

## Decision 6: Como testar FR-013

**Decision**: Dois níveis de teste, ambos com pytest e sem dependência nova:

1. Testes rápidos com a criação de sockets substituída por uma função que falha, convertendo a fixture de artigo com MarkItDown e com pymupdf4llm (este último pulado quando não instalado).
2. Testes de configuração: as opções de pipeline do Docling têm serviços remotos desligados; a variável de telemetria do hub está definida depois de importar o módulo de motores; o ambiente passado ao processo do MinerU contém a variável de telemetria (capturado substituindo a execução do subprocesso).

**Rationale**: O teste com sockets bloqueados prova o comportamento dos motores leves. Para o Docling, a conversão real depende de modelos baixados e fica no teste lento já existente (fonte: `tests/test_core.py:99`); o teste de configuração cobre o que este projeto controla.
**Alternatives considered**: Rodar o Docling com sockets bloqueados no teste rápido (falharia sem os modelos em cache); sandbox de rede externa ao pytest (não roda no `uv run pytest` comum).

## Decision 7: Como testar o MinerU sem instalá-lo (FR-006 a FR-009)

**Decision**: Substituir, nos testes, a localização do executável e a execução do subprocesso, simulando as saídas de versão e de conversão; limpar o cache de verificação de versão entre os testes.
**Rationale**: O MinerU é opcional e pesado (fonte: `README.md:18`); o adaptador concentra a interação em duas chamadas de subprocesso (fonte: `src/artigo2md/engines.py:136`) (fonte: `src/artigo2md/engines.py:162`) e guarda o resultado da verificação em cache por executável (fonte: `src/artigo2md/engines.py:124`).
**Alternatives considered**: Exigir o MinerU instalado para testar (contraria a instrução do projeto de não instalá-lo sem pedido).

## Decision 8: Como testar pymupdf4llm ausente, OCR e reuso do conversor (FR-005, FR-002, FR-012)

**Decision**:
- FR-005: simular a ausência do pacote no import e verificar o erro de motor indisponível com a instrução de instalação.
- FR-002: `[PROPOSTA — a validar na implementacao]` verificar a classe das opções de OCR devolvida para plataforma macOS e para outra plataforma, simulando a plataforma.
- FR-012: `[PROPOSTA — a validar na implementacao]` verificar que duas chamadas para o mesmo modo devolvem o mesmo conversor. Se criar o conversor do Docling já carregar modelos, o teste deve substituir a construção ou ir para o grupo lento.

**Rationale**: São os três requisitos restantes sem teste. O custo de instanciar o conversor do Docling não foi medido nesta pesquisa, daí a ressalva.
**Alternatives considered**: Deixar FR-002 e FR-012 só no teste lento (demora e não roda por padrão).
