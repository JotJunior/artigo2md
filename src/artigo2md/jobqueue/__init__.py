"""Fila de conversão: worker nativo que consome jobs do RabbitMQ local.

Este subpacote só é importado pelo subcomando `artigo2md worker` (ver `cli.py`). Ele NÃO importa,
neste arquivo, `pika`, `psutil` nem os motores de conversão: `convert`, `doctor` e `serve` não podem
carregar a fila (FR-017), e o processo principal do worker nunca carrega Docling nem pymupdf4llm (FR-006).
"""
