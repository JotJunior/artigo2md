"""Linha de comando: `artigo2md convert`, `serve`, `doctor`, `worker` (fila de conversão) e `models`."""
from __future__ import annotations

import argparse
import functools
import json
import platform
import re
import sys
from pathlib import Path

from . import __version__

# Endereços em que a interface só atende a própria máquina.
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def _collect_pdfs(inputs: list[str]) -> list[tuple[Path, Path]]:
    """Devolve pares (PDF, caminho do Markdown relativo à pasta de saída).

    PDFs achados dentro de uma pasta de entrada mantêm as subpastas dela, para que
    arquivos de mesmo nome em pastas diferentes não disputem a mesma saída; um PDF
    passado diretamente vai para a raiz da pasta de saída.
    """
    pdfs: list[tuple[Path, Path]] = []
    for item in inputs:
        p = Path(item).expanduser()
        if p.is_dir():
            found = sorted(x for x in p.rglob("*") if x.suffix.lower() == ".pdf")
            pdfs.extend((x, x.relative_to(p).with_suffix(".md")) for x in found)
        elif p.suffix.lower() == ".pdf" and p.exists():
            pdfs.append((p, Path(p.stem + ".md")))
        else:
            print(f"ignorado (não é PDF ou não existe): {p}", file=sys.stderr)
    return pdfs


def _find_collisions(pdfs: list[tuple[Path, Path]]) -> dict[Path, list[Path]]:
    """Saídas pedidas por mais de um PDF diferente: {caminho de saída: [PDFs de origem]}."""
    by_target: dict[Path, list[Path]] = {}
    for pdf, relative in pdfs:
        sources = by_target.setdefault(relative, [])
        if pdf.resolve() not in (s.resolve() for s in sources):
            sources.append(pdf)
    return {target: sources for target, sources in by_target.items() if len(sources) > 1}


def cmd_convert(args: argparse.Namespace) -> int:
    from .router import convert

    pdfs = _collect_pdfs(args.inputs)
    if not pdfs:
        print("Nenhum PDF encontrado.", file=sys.stderr)
        return 1
    collisions = _find_collisions(pdfs)
    if collisions:
        print("Entradas diferentes gerariam o mesmo arquivo de saída; nada foi convertido:",
              file=sys.stderr)
        for target, sources in collisions.items():
            print(f"  {target} ← {', '.join(str(s) for s in sources)}", file=sys.stderr)
        print("Passe a pasta que contém esses arquivos (a saída repete as subpastas dela) "
              "ou converta-os em execuções separadas, com pastas de saída diferentes.",
              file=sys.stderr)
        return 1
    out_dir = Path(args.output).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for i, (pdf, relative) in enumerate(pdfs, 1):
        target = out_dir / relative
        if target.exists() and not args.overwrite:
            print(f"[{i}/{len(pdfs)}] já existe, pulando: {relative}")
            continue
        print(f"[{i}/{len(pdfs)}] {relative.with_suffix(pdf.suffix)}")
        try:
            result = convert(pdf, engine=args.engine, progress=lambda m: print(f"    {m}"))
        except Exception as exc:
            failures += 1
            print(f"    ERRO: {exc}", file=sys.stderr)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(result.markdown, encoding="utf-8")
        if args.report:
            target.with_suffix(".report.json").write_text(
                json.dumps(result.report(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        last = result.attempts[-1]
        score = f"{last.quality.score:.0f}/100" if last.quality else "?"
        print(f"    → {target}  (motor: {result.engine}, qualidade: {score})")
    print(f"Concluído: {len(pdfs) - failures} ok, {failures} com erro.")
    return 1 if failures else 0


def cmd_serve(args: argparse.Namespace) -> int:
    import socket

    import uvicorn

    from .web import create_app

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            print(f"A porta {args.port} já está em uso por outro programa. "
                  f"Escolha outra, por exemplo: artigo2md serve --port {args.port + 1}",
                  file=sys.stderr)
            return 1

    app = create_app(Path(args.data_dir).expanduser(), idle_timeout=args.idle_timeout)
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}"
    print(f"artigo2md {__version__} — interface em {url}")
    if args.host not in LOCAL_HOSTS:
        print(f"Atenção: a interface não tem senha e fica acessível a outras máquinas da rede "
              f"em {args.host}:{args.port}. Não a exponha à internet aberta.", file=sys.stderr)
    if args.open:
        import threading
        import webbrowser

        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    from .engines import engine_table

    table = engine_table()
    print(f"artigo2md {__version__} · Python {platform.python_version()} · {platform.system()} {platform.machine()}")
    for e in table:
        if e["installed"]:
            tag = "ok"
        elif e["required"]:
            tag = "AUSENTE"
        else:
            tag = f"não instalado (opcional: {e['install']})"
        print(f"  {e['name']:<12} {e['license']:<9} {tag}")
    required_ok = all(e["installed"] for e in table if e["required"])
    print("Pronto para uso." if required_ok else "Faltam motores obrigatórios: rode `uv sync`.")
    _print_infra_section()
    return 0 if required_ok else 1


def _print_infra_section() -> None:
    """Seção opcional de infraestrutura (versão desktop). Nunca altera o código de saída."""
    from . import infra  # import local: `convert` nunca carrega este módulo

    try:
        for line in infra.format_section(infra.collect()):
            print(line)
    except Exception:  # defensivo: a infraestrutura é opcional e não pode quebrar o doctor
        print("Infraestrutura (versão desktop, opcional): não foi possível verificar.")


# ── worker da fila ──────────────────────────────────────────────────────────────────────────────
MISSING_EXTRA = "O worker precisa das dependências da fila. Instale com: uv sync --extra queue"
DATA_DIR_REQUIRED = "a opção --data-dir é obrigatória (pasta onde o worker grava o Markdown)"


def _number_option(kind: type, label: str):
    """Conversor de opção numérica com mensagem em português (o argparse só diz "invalid ... value")."""

    def convert(text: str):
        try:
            return kind(text)
        except ValueError:
            noun = "um número inteiro" if kind is int else "um número"
            raise argparse.ArgumentTypeError(f"valor inválido ({text!r}): deve ser {noun}.") from None

    convert.__name__ = label
    return convert


def _worker_parser_error(parser: argparse.ArgumentParser, message: str):
    """Erro de uso do subcomando `worker` em português (código de saída 2, como o argparse)."""
    message = re.sub(r"^argument ", "argumento ", message)
    parser.print_usage(sys.stderr)
    print(f"{parser.prog}: erro: {message}", file=sys.stderr)
    raise SystemExit(2)


def cmd_worker(args: argparse.Namespace) -> int:
    from .jobqueue import settings as queue_settings  # import local: só este ramo carrega a fila (FR-017)

    def usage_error(message: str) -> int:
        print(f"{args._parser.prog}: erro: {message}", file=sys.stderr)
        return queue_settings.EXIT_USAGE

    if not args.data_dir:
        return usage_error(DATA_DIR_REQUIRED)
    try:
        import pika  # noqa: F401
        import psutil  # noqa: F401
    except ImportError:
        print(MISSING_EXTRA, file=sys.stderr)
        return queue_settings.EXIT_EXTRA_MISSING

    try:
        settings = queue_settings.load_settings(
            data_dir=args.data_dir,
            workers=queue_settings.DEFAULT_WORKERS if args.workers is None else args.workers,
            min_free_memory_mb=(queue_settings.DEFAULT_MIN_FREE_MEMORY_MB
                                if args.min_free_memory is None else args.min_free_memory),
            retry_delay=args.retry_delay,
            max_attempts=args.max_attempts,
            idle_timeout=args.idle_timeout,
            shutdown_timeout=args.shutdown_timeout,
            conversion_timeout=args.conversion_timeout,
        )
    except queue_settings.ConfigError as exc:
        print(exc.message, file=sys.stderr)
        return exc.exit_code

    from .jobqueue.service import Service

    print(f"artigo2md {__version__} — worker com {settings.workers} conversor(es); "
          f"RabbitMQ em {settings.host}:{settings.port}; Markdown em {settings.data_dir}", file=sys.stderr)
    return Service(settings).run()


# ── modelos de conversão ────────────────────────────────────────────────────────────────────────
def cmd_models(args: argparse.Namespace) -> int:
    from . import modelcache  # import local: só este ramo carrega o módulo (e nunca o docling) — FR-039

    return modelcache.run(args.verb, as_json=args.json)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="artigo2md", description="PDF → Markdown, local.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("convert", help="converte PDFs ou pastas de PDFs")
    c.add_argument("inputs", nargs="+", help="arquivos .pdf e/ou pastas (busca recursiva)")
    c.add_argument("-o", "--output", default="markdown", help="pasta de saída (padrão: ./markdown)")
    c.add_argument("-e", "--engine", default="auto", choices=["auto", "docling", "pymupdf", "mineru", "markitdown"])
    c.add_argument("--overwrite", action="store_true", help="reconverte mesmo se o .md já existir")
    c.add_argument("--report", action="store_true", help="grava também um .report.json por arquivo")
    c.set_defaults(func=cmd_convert)

    s = sub.add_parser("serve", help="abre a interface web")
    s.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 para acessar de outras máquinas")
    s.add_argument("--port", type=int, default=8777)
    s.add_argument("--data-dir", default="~/artigo2md-dados", help="onde ficam uploads e resultados")
    s.add_argument("--open", action="store_true", help="abre o navegador automaticamente")
    s.add_argument("--idle-timeout", type=float, default=300, metavar="SEG",
                   help="encerra o conversor após SEG segundos sem uso, liberando a RAM dos "
                        "modelos (padrão: 300; 0 = nunca)")
    s.set_defaults(func=cmd_serve)

    d = sub.add_parser("doctor", help="verifica a instalação e os motores disponíveis")
    d.set_defaults(func=cmd_doctor)

    w = sub.add_parser("worker", help="consome jobs de conversão do RabbitMQ local (fila)",
                       description="Consome jobs do RabbitMQ local (127.0.0.1), converte cada PDF e grava o "
                                   "Markdown em --data-dir/<job_id>/. Requer o extra `queue` "
                                   "(uv sync --extra queue) e a infraestrutura da stack no ar.")
    w.add_argument("--data-dir", metavar="PASTA",
                   help="pasta onde o worker grava o Markdown (obrigatória)")
    w.add_argument("--workers", type=_number_option(int, "--workers"), metavar="N",
                   help="conversões simultâneas (padrão: 4, o maior pool medido; ver "
                        "docs/specs/conversion-queue/ram-measurement.md)")
    w.add_argument("--min-free-memory", type=_number_option(int, "--min-free-memory"), metavar="MB",
                   help="memória livre mínima, em MB, para iniciar um conversor "
                        "(padrão: 4822, derivado da medição em docs/specs/conversion-queue/ram-measurement.md)")
    w.add_argument("--retry-delay", type=_number_option(float, "--retry-delay"), default=30.0, metavar="SEG",
                   help="espera antes de uma nova tentativa (padrão: 30)")
    w.add_argument("--max-attempts", type=_number_option(int, "--max-attempts"), default=3, metavar="N",
                   help="tentativas por job, a primeira incluída (padrão: 3; 1 = sem nova tentativa)")
    w.add_argument("--idle-timeout", type=_number_option(float, "--idle-timeout"), default=300.0, metavar="SEG",
                   help="encerra o conversor após SEG segundos sem uso (padrão: 300; 0 = nunca)")
    w.add_argument("--shutdown-timeout", type=_number_option(float, "--shutdown-timeout"), default=30.0,
                   metavar="SEG", help="prazo para o job em andamento ao receber SIGINT/SIGTERM (padrão: 30)")
    w.add_argument("--conversion-timeout", type=_number_option(float, "--conversion-timeout"), default=0.0,
                   metavar="SEG", help="limite de tempo por conversão (padrão: 0 = sem limite; se usar, abaixo de 1800 s, o prazo do broker)")
    w.error = functools.partial(_worker_parser_error, w)
    w.set_defaults(func=cmd_worker, _parser=w)

    m = sub.add_parser("models", help="verifica ou baixa os modelos do Docling (macOS)",
                       description="Verifica (status) ou baixa (download) os modelos que o Docling usa, no "
                                   "cache padrão do Hugging Face. Com --json, escreve um evento JSON por linha.")
    m.add_argument("verb", choices=["status", "download"], help="status: o que já está no computador; "
                                                                 "download: baixa só o que falta")
    m.add_argument("--json", action="store_true", help="eventos JSON Lines em stdout (uso do app desktop)")
    m.set_defaults(func=cmd_models)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
