"""Linha de comando: `artigo2md convert`, `artigo2md serve`, `artigo2md doctor`."""
from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

from . import __version__


def _collect_pdfs(inputs: list[str]) -> list[Path]:
    pdfs: list[Path] = []
    for item in inputs:
        p = Path(item).expanduser()
        if p.is_dir():
            pdfs.extend(sorted(x for x in p.rglob("*") if x.suffix.lower() == ".pdf"))
        elif p.suffix.lower() == ".pdf" and p.exists():
            pdfs.append(p)
        else:
            print(f"ignorado (não é PDF ou não existe): {p}", file=sys.stderr)
    return pdfs


def cmd_convert(args: argparse.Namespace) -> int:
    from .router import convert

    pdfs = _collect_pdfs(args.inputs)
    if not pdfs:
        print("Nenhum PDF encontrado.", file=sys.stderr)
        return 1
    out_dir = Path(args.output).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for i, pdf in enumerate(pdfs, 1):
        target = out_dir / (pdf.stem + ".md")
        if target.exists() and not args.overwrite:
            print(f"[{i}/{len(pdfs)}] já existe, pulando: {target.name}")
            continue
        print(f"[{i}/{len(pdfs)}] {pdf.name}")
        try:
            result = convert(pdf, engine=args.engine, progress=lambda m: print(f"    {m}"))
        except Exception as exc:
            failures += 1
            print(f"    ERRO: {exc}", file=sys.stderr)
            continue
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

    app = create_app(Path(args.data_dir).expanduser())
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}"
    print(f"artigo2md {__version__} — interface em {url}")
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
    return 0 if required_ok else 1


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
    s.set_defaults(func=cmd_serve)

    d = sub.add_parser("doctor", help="verifica a instalação e os motores disponíveis")
    d.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
