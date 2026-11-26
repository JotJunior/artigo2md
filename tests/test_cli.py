"""Testes da linha de comando: `artigo2md convert`, `serve` e `doctor`.

A função de entrada é chamada em processo, com a lista de argumentos, e devolve o
código de saída (ver docs/specs/command-line-interface/research.md, Decision 4).
"""
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from artigo2md import __version__, cli, engines, router


@pytest.fixture
def run_cli(capsys):
    """Chama a linha de comando e devolve (código de saída, stdout, stderr)."""

    def _run(*args) -> tuple[int, str, str]:
        code = cli.main([str(arg) for arg in args])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


@pytest.fixture
def fake_convert(monkeypatch):
    """Troca a conversão real por uma simulação rápida e registra cada chamada.

    Serve aos testes que exercitam a lógica da linha de comando, não a conversão.
    Arquivos cujo nome começa com "falha" simulam um erro de conversão.
    """
    calls: list[dict] = []

    def _convert(path, engine="auto", progress=None):
        path = Path(path)
        calls.append({"path": path, "engine": engine})
        if path.stem.startswith("falha"):
            raise RuntimeError("falha simulada")
        if progress:
            progress("Convertendo (simulado)")
        quality = router.Quality(
            score=100.0, accepted=True, tables_found=0, tables_expected=0, chars_per_page=0.0
        )
        return router.ConversionResult(
            source=str(path),
            markdown=f"# {path.stem}\n\nconteúdo simulado\n",
            engine="simulado",
            analysis={"pages": 1},
            attempts=[router.Attempt("simulado", 0.0, quality)],
        )

    monkeypatch.setattr(router, "convert", _convert)
    return calls


@pytest.mark.parametrize("input_kind", ["file", "folder"])
def test_convert_end_to_end_with_markitdown(run_cli, article_pdf, tmp_path, input_kind):
    source = article_pdf if input_kind == "file" else article_pdf.parent
    out_dir = tmp_path / "saida"

    code, out, _err = run_cli("convert", source, "-o", out_dir, "-e", "markitdown")

    assert code == 0
    markdown = out_dir / "artigo.md"
    assert markdown.exists()
    assert "randomized trial" in markdown.read_text(encoding="utf-8")
    assert "motor: markitdown" in out
    assert "Concluído: 1 ok, 0 com erro." in out


def test_fake_convert_replaces_real_conversion(run_cli, fake_convert, article_pdf, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    code, out, _err = run_cli("convert", article_pdf, "-e", "docling")

    assert code == 0
    assert fake_convert == [{"path": article_pdf, "engine": "docling"}]
    # sem -o, a saída vai para a pasta "markdown" do diretório atual
    assert (tmp_path / "markdown" / "artigo.md").read_text(encoding="utf-8").startswith("# artigo")
    assert "motor: simulado" in out


def test_convert_defaults_to_auto_engine(run_cli, fake_convert, article_pdf, tmp_path):
    code, _out, _err = run_cli("convert", article_pdf, "-o", tmp_path / "saida")

    assert code == 0
    assert fake_convert[0]["engine"] == "auto"


def _touch_pdf(path: Path) -> Path:
    """Cria um arquivo com extensão de PDF; o conteúdo não importa com a conversão simulada."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4 simulado")
    return path


# ── convert ──────────────────────────────────────────────────────────────────
def test_convert_searches_folders_recursively_for_pdfs(run_cli, fake_convert, tmp_path):
    source = tmp_path / "entrada"
    first = _touch_pdf(source / "a.pdf")
    nested = _touch_pdf(source / "sub" / "fundo" / "b.PDF")
    (source / "notas.txt").write_text("não é pdf", encoding="utf-8")

    code, _out, _err = run_cli("convert", source, "-o", tmp_path / "saida")

    assert code == 0
    assert sorted(call["path"] for call in fake_convert) == sorted([first, nested])


def test_convert_ignores_inputs_that_are_not_pdfs(run_cli, fake_convert, tmp_path):
    valid = _touch_pdf(tmp_path / "bom.pdf")
    text_file = tmp_path / "notas.txt"
    text_file.write_text("não é pdf", encoding="utf-8")
    missing = tmp_path / "sumiu.pdf"

    code, _out, err = run_cli("convert", valid, text_file, missing, "-o", tmp_path / "saida")

    assert code == 0
    assert [call["path"] for call in fake_convert] == [valid]
    assert f"ignorado (não é PDF ou não existe): {text_file}" in err
    assert f"ignorado (não é PDF ou não existe): {missing}" in err


def test_convert_without_any_pdf_exits_with_error(run_cli, fake_convert, tmp_path):
    code, _out, err = run_cli("convert", tmp_path / "sumiu.pdf", "-o", tmp_path / "saida")

    assert code == 1
    assert fake_convert == []
    assert "Nenhum PDF encontrado." in err
    assert not (tmp_path / "saida").exists()


def test_convert_skips_existing_output_unless_overwrite(run_cli, fake_convert, tmp_path):
    source = _touch_pdf(tmp_path / "artigo.pdf")
    out_dir = tmp_path / "saida"

    first_code, _out, _err = run_cli("convert", source, "-o", out_dir)
    second_code, second_out, _err = run_cli("convert", source, "-o", out_dir)

    assert (first_code, second_code) == (0, 0)  # pular não é erro, nem quando todos são pulados
    assert len(fake_convert) == 1
    assert "já existe, pulando: artigo.md" in second_out

    third_code, _out, _err = run_cli("convert", source, "-o", out_dir, "--overwrite")

    assert third_code == 0
    assert len(fake_convert) == 2


def test_convert_report_writes_json_next_to_markdown(run_cli, fake_convert, tmp_path):
    source = _touch_pdf(tmp_path / "artigo.pdf")
    out_dir = tmp_path / "saida"

    code, _out, _err = run_cli("convert", source, "-o", out_dir, "--report")

    assert code == 0
    report = json.loads((out_dir / "artigo.report.json").read_text(encoding="utf-8"))
    assert report["source"] == str(source)
    assert report["engine"] == "simulado"
    assert report["analysis"] == {"pages": 1}
    assert report["attempts"][0]["quality"]["score"] == 100.0
    assert "markdown" not in report


def test_convert_without_report_writes_no_json(run_cli, fake_convert, tmp_path):
    source = _touch_pdf(tmp_path / "artigo.pdf")
    out_dir = tmp_path / "saida"

    run_cli("convert", source, "-o", out_dir)

    assert not (out_dir / "artigo.report.json").exists()


def test_convert_failure_in_one_pdf_does_not_stop_the_others(run_cli, fake_convert, tmp_path):
    source = tmp_path / "entrada"
    _touch_pdf(source / "falha-primeiro.pdf")
    _touch_pdf(source / "ok-depois.pdf")
    out_dir = tmp_path / "saida"

    code, out, err = run_cli("convert", source, "-o", out_dir)

    assert code == 1
    assert len(fake_convert) == 2
    assert "ERRO: falha simulada" in err
    assert "Concluído: 1 ok, 1 com erro." in out
    assert (out_dir / "ok-depois.md").exists()
    assert not (out_dir / "falha-primeiro.md").exists()


# ── serve ────────────────────────────────────────────────────────────────────
@pytest.fixture
def serve_env(monkeypatch):
    """Simula servidor, aplicação web, temporizador e navegador; registra o que o serve pediu."""
    import threading
    import webbrowser

    import uvicorn

    from artigo2md import web

    seen = {"apps": [], "idle_timeouts": [], "runs": [], "opened": []}

    def _create_app(data_dir, idle_timeout=None):
        seen["apps"].append(data_dir)
        seen["idle_timeouts"].append(idle_timeout)
        return object()

    def _run(app, **kwargs):
        seen["runs"].append(kwargs)

    class _ImmediateTimer:
        """Executa a função na hora, para o navegador simulado não ser chamado depois do teste."""

        def __init__(self, _interval, function):
            self._function = function

        def start(self):
            self._function()

    monkeypatch.setattr(web, "create_app", _create_app)
    monkeypatch.setattr(uvicorn, "run", _run)
    monkeypatch.setattr(threading, "Timer", _ImmediateTimer)
    monkeypatch.setattr(webbrowser, "open", lambda url: seen["opened"].append(url))
    return seen


@pytest.fixture
def port_is_free(monkeypatch):
    """Faz a sondagem de porta do serve responder "livre", sem depender do que roda na máquina."""
    probed = []

    class _FreePortProbe:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def connect_ex(self, address):
            probed.append(address)
            return 1

    monkeypatch.setattr(socket, "socket", _FreePortProbe)
    return probed


def test_serve_defaults_to_local_host_and_default_port(run_cli, serve_env, port_is_free):
    code, out, _err = run_cli("serve")

    assert code == 0
    assert port_is_free == [("127.0.0.1", 8777)]
    assert serve_env["apps"] == [Path("~/artigo2md-dados").expanduser()]
    assert serve_env["idle_timeouts"] == [300]  # padrão: descarrega o conversor após 5 minutos ocioso
    assert serve_env["runs"] == [{"host": "127.0.0.1", "port": 8777, "log_level": "warning"}]
    assert "interface em http://localhost:8777" in out
    assert serve_env["opened"] == []


def test_serve_open_and_data_dir_options(run_cli, serve_env, port_is_free, tmp_path):
    data_dir = tmp_path / "dados"

    code, _out, _err = run_cli(
        "serve", "--open", "--data-dir", data_dir, "--port", "9001", "--idle-timeout", "0"
    )

    assert code == 0
    assert serve_env["apps"] == [data_dir]
    assert serve_env["idle_timeouts"] == [0]
    assert serve_env["runs"][0]["port"] == 9001
    assert serve_env["opened"] == ["http://localhost:9001"]


def test_serve_port_in_use_exits_with_suggestion(run_cli, serve_env):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]

        code, _out, err = run_cli("serve", "--port", port)

    assert code == 1
    assert f"A porta {port} já está em uso" in err
    assert f"artigo2md serve --port {port + 1}" in err
    assert serve_env["runs"] == []


# ── doctor e versão ──────────────────────────────────────────────────────────
def _engine_row(name, required, installed, install=None, license="MIT"):
    return {"id": name.lower(), "name": name, "license": license, "required": required,
            "installed": installed, "install": install, "detail": None}


def test_doctor_reports_ready_when_required_engines_are_installed(run_cli, monkeypatch):
    table = [
        _engine_row("Docling", required=True, installed=True),
        _engine_row("MinerU", required=False, installed=False, install="instale à parte",
                    license="AGPL-3.0"),
    ]
    monkeypatch.setattr(engines, "engine_table", lambda: table)

    code, out, _err = run_cli("doctor")

    assert code == 0
    lines = out.splitlines()
    assert lines[0].startswith(f"artigo2md {__version__} · Python ")
    assert lines[1].split() == ["Docling", "MIT", "ok"]
    assert lines[2].split(maxsplit=2) == ["MinerU", "AGPL-3.0", "não instalado (opcional: instale à parte)"]
    # a seção de infraestrutura vem depois (tests/test_infra.py e os testes abaixo)
    assert lines[3] == "Pronto para uso."


def test_doctor_fails_when_a_required_engine_is_missing(run_cli, monkeypatch):
    table = [_engine_row("Docling", required=True, installed=False)]
    monkeypatch.setattr(engines, "engine_table", lambda: table)

    code, out, _err = run_cli("doctor")

    assert code == 1
    assert out.splitlines()[1].split() == ["Docling", "MIT", "AUSENTE"]
    assert "Faltam motores obrigatórios" in out


# ── doctor: seção de infraestrutura (container-stack, FR-009/FR-010) ─────────────
@pytest.fixture
def infra_down(monkeypatch, tmp_path):
    """Aponta as três portas para endereços sem ninguém ouvindo e isola o `./stack/.env`."""
    monkeypatch.chdir(tmp_path)
    ports = []
    for var in ("ARTIGO2MD_POSTGRES_PORT", "ARTIGO2MD_RABBITMQ_PORT", "ARTIGO2MD_API_PORT"):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        monkeypatch.setenv(var, str(port))
        ports.append(port)
    return ports


@pytest.mark.parametrize("required_installed", [True, False])
def test_doctor_exit_code_ignores_infrastructure_state(
    run_cli, monkeypatch, infra_down, required_installed
):
    table = [_engine_row("Docling", required=True, installed=required_installed)]
    monkeypatch.setattr(engines, "engine_table", lambda: table)

    code, out, _err = run_cli("doctor")

    assert code == (0 if required_installed else 1)
    lines = out.splitlines()
    start = lines.index("Infraestrutura (versão desktop, opcional):")
    assert [line.split()[0] for line in lines[start + 1 : start + 4]] == ["postgres", "rabbitmq", "api"]
    assert all("inacessível" in line for line in lines[start + 1 : start + 4])
    assert lines[-1].startswith("Para subir a infraestrutura: sh stack/up.sh")


def test_doctor_omits_instruction_when_all_services_are_reachable(run_cli, monkeypatch, infra_down):
    table = [_engine_row("Docling", required=True, installed=True)]
    monkeypatch.setattr(engines, "engine_table", lambda: table)
    from artigo2md import infra

    reachable = [
        infra.InfraReport(name, "127.0.0.1", 1000 + i, infra.REACHABLE, "acessível")
        for i, name in enumerate(["postgres", "rabbitmq", "api"])
    ]
    monkeypatch.setattr(infra, "collect", lambda *a, **k: reachable)

    code, out, _err = run_cli("doctor")

    assert code == 0
    assert "acessível" in out
    assert "Para subir a infraestrutura" not in out


def test_doctor_survives_an_unexpected_infrastructure_failure(run_cli, monkeypatch):
    table = [_engine_row("Docling", required=True, installed=True)]
    monkeypatch.setattr(engines, "engine_table", lambda: table)
    from artigo2md import infra

    def boom(*_a, **_k):
        raise RuntimeError("falha inesperada")

    monkeypatch.setattr(infra, "collect", boom)

    code, out, _err = run_cli("doctor")

    assert code == 0
    assert "Pronto para uso." in out
    assert "não foi possível verificar" in out


def test_convert_does_not_touch_the_infrastructure(run_cli, fake_convert, article_pdf, tmp_path, monkeypatch):
    from artigo2md import infra

    def forbidden(*_a, **_k):
        raise AssertionError("convert não pode consultar a infraestrutura")

    monkeypatch.setattr(infra, "collect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)

    code, out, _err = run_cli("convert", article_pdf, "-o", tmp_path / "saida")

    assert code == 0
    assert "Infraestrutura" not in out


def test_convert_path_does_not_import_the_infrastructure_module():
    import subprocess
    import sys

    code = (
        "import sys\n"
        "from artigo2md import cli\n"
        "assert 'artigo2md.infra' not in sys.modules\n"
        "cli.main(['convert', '/caminho/que/nao/existe.pdf'])\n"
        "assert 'artigo2md.infra' not in sys.modules\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)

    assert proc.returncode == 0, proc.stderr


def test_version_option_prints_version_and_exits(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["--version"])

    assert info.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


# ── convert: PDFs de mesmo nome em subpastas (FR-014) ────────────────────────
@pytest.fixture
def same_named_pdfs(tmp_path):
    """Pasta de entrada com dois PDFs de mesmo nome em subpastas diferentes."""
    source = tmp_path / "entrada"
    _touch_pdf(source / "2023" / "artigo.pdf")
    _touch_pdf(source / "2024" / "artigo.pdf")
    return source


def test_convert_mirrors_input_subfolders_for_same_named_pdfs(run_cli, fake_convert, same_named_pdfs, tmp_path):
    out_dir = tmp_path / "saida"

    code, out, _err = run_cli("convert", same_named_pdfs, "-o", out_dir)

    assert code == 0
    assert len(fake_convert) == 2
    assert (out_dir / "2023" / "artigo.md").exists()
    assert (out_dir / "2024" / "artigo.md").exists()
    assert not (out_dir / "artigo.md").exists()
    assert "pulando" not in out


def test_convert_direct_file_goes_to_output_root(run_cli, fake_convert, same_named_pdfs, tmp_path):
    out_dir = tmp_path / "saida"

    code, _out, _err = run_cli("convert", same_named_pdfs / "2023" / "artigo.pdf", "-o", out_dir)

    assert code == 0
    assert (out_dir / "artigo.md").exists()
    assert not (out_dir / "2023").exists()


def test_convert_report_stays_next_to_mirrored_markdown(run_cli, fake_convert, same_named_pdfs, tmp_path):
    out_dir = tmp_path / "saida"

    code, _out, _err = run_cli("convert", same_named_pdfs, "-o", out_dir, "--report")

    assert code == 0
    for year in ("2023", "2024"):
        report = json.loads((out_dir / year / "artigo.report.json").read_text(encoding="utf-8"))
        assert report["source"] == str(same_named_pdfs / year / "artigo.pdf")


def test_convert_rerun_skips_mirrored_outputs(run_cli, fake_convert, same_named_pdfs, tmp_path):
    out_dir = tmp_path / "saida"

    run_cli("convert", same_named_pdfs, "-o", out_dir)
    code, out, _err = run_cli("convert", same_named_pdfs, "-o", out_dir)

    assert code == 0
    assert len(fake_convert) == 2  # nada foi reconvertido na segunda execução
    assert "já existe, pulando: 2023/artigo.md" in out
    assert "já existe, pulando: 2024/artigo.md" in out


# ── convert: entradas diferentes que apontam para a mesma saída (FR-016) ─────
def test_convert_refuses_direct_files_with_the_same_name(run_cli, fake_convert, same_named_pdfs, tmp_path):
    first = same_named_pdfs / "2023" / "artigo.pdf"
    second = same_named_pdfs / "2024" / "artigo.pdf"
    out_dir = tmp_path / "saida"

    code, _out, err = run_cli("convert", first, second, "-o", out_dir)

    assert code == 1
    assert fake_convert == []  # nada é convertido quando há colisão
    assert not out_dir.exists()
    assert "mesmo arquivo de saída" in err
    assert "artigo.md" in err and str(first) in err and str(second) in err
    assert "Passe a pasta que contém esses arquivos" in err


def test_convert_refuses_folders_with_the_same_relative_path(run_cli, fake_convert, tmp_path):
    _touch_pdf(tmp_path / "lote-a" / "sub" / "artigo.pdf")
    _touch_pdf(tmp_path / "lote-b" / "sub" / "artigo.pdf")

    code, _out, err = run_cli("convert", tmp_path / "lote-a", tmp_path / "lote-b", "-o", tmp_path / "saida")

    assert code == 1
    assert fake_convert == []
    assert "sub/artigo.md" in err


def test_convert_same_file_given_twice_is_not_a_collision(run_cli, fake_convert, tmp_path):
    source = _touch_pdf(tmp_path / "artigo.pdf")

    code, out, _err = run_cli("convert", source, source, "-o", tmp_path / "saida")

    assert code == 0
    assert len(fake_convert) == 1
    assert "já existe, pulando: artigo.md" in out


# ── serve: aviso quando a interface fica visível na rede (FR-015) ────────────
def test_serve_warns_when_host_is_not_local(run_cli, serve_env, port_is_free):
    code, _out, err = run_cli("serve", "--host", "0.0.0.0")

    assert code == 0
    assert serve_env["runs"][0]["host"] == "0.0.0.0"
    assert "não tem senha" in err
    assert "outras máquinas da rede" in err


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_serve_does_not_warn_for_local_hosts(run_cli, serve_env, port_is_free, host):
    code, _out, err = run_cli("serve", "--host", host)

    assert code == 0
    assert err == ""


# ── worker: opções, extra ausente e códigos de saída (tarefas 5.3.1 a 5.3.4) ─────────────────────────
@pytest.fixture
def worker_env(monkeypatch, tmp_path):
    """Ambiente limpo para `artigo2md worker`: sem credenciais herdadas, sem `stack/.env`, sem serviço real."""
    for var in ("RABBITMQ_DEFAULT_USER", "RABBITMQ_DEFAULT_PASS", "ARTIGO2MD_STACK_ENV"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    data = tmp_path / "dados"
    data.mkdir()
    return SimpleNamespace(data=data, tmp=tmp_path, monkeypatch=monkeypatch)


@pytest.fixture
def fake_service(monkeypatch):
    """Troca `Service` por um dublê que registra as configurações e devolve o código de saída escolhido."""
    pytest.importorskip("pika")
    pytest.importorskip("psutil")
    from artigo2md.jobqueue import service

    state = SimpleNamespace(exit_code=0, settings=[])

    class Dummy:
        def __init__(self, settings):
            state.settings.append(settings)

        def run(self):
            return state.exit_code

    monkeypatch.setattr(service, "Service", Dummy)
    return state


def _worker_args(env, *extra, workers=2, memory=500):
    return ["worker", "--data-dir", env.data, "--workers", workers, "--min-free-memory", memory, *extra]


def test_worker_requires_data_dir(run_cli, worker_env):
    code, _out, err = run_cli("worker", "--workers", 1, "--min-free-memory", 0)
    assert code == 2
    assert "a opção --data-dir é obrigatória (pasta onde o worker grava o Markdown)" in err


def test_worker_without_the_ram_options_uses_the_defaults_derived_from_the_measurement(
        run_cli, worker_env, fake_service):
    from artigo2md.jobqueue import settings as queue_settings

    worker_env.monkeypatch.setenv("RABBITMQ_DEFAULT_USER", "u")
    worker_env.monkeypatch.setenv("RABBITMQ_DEFAULT_PASS", "p")
    code, _out, err = run_cli("worker", "--data-dir", worker_env.data)
    assert code == 0 and "medição" not in err  # a recusa "medição pendente" deixou de valer (tarefa 7.3)
    (settings,) = fake_service.settings
    assert settings.workers == queue_settings.DEFAULT_WORKERS
    assert settings.min_free_memory_mb == queue_settings.DEFAULT_MIN_FREE_MEMORY_MB


@pytest.mark.parametrize("extra,fragment", [
    (["--retry-delay", "0"], "--retry-delay inválido"),
    (["--max-attempts", "0"], "--max-attempts inválido"),
    (["--idle-timeout", "-1"], "--idle-timeout inválido"),
    (["--shutdown-timeout", "-1"], "--shutdown-timeout inválido"),
    (["--conversion-timeout", "-1"], "--conversion-timeout inválido"),
])
def test_worker_rejects_invalid_values_with_exit_2(run_cli, worker_env, fake_service, extra, fragment):
    code, _out, err = run_cli(*_worker_args(worker_env, *extra))
    assert code == 2 and fragment in err and fake_service.settings == []


def test_worker_rejects_non_positive_workers_and_negative_memory(run_cli, worker_env, fake_service):
    assert run_cli(*_worker_args(worker_env, workers=0))[0] == 2
    assert run_cli(*_worker_args(worker_env, memory=-1))[0] == 2


def test_worker_rejects_non_numeric_values_in_portuguese(run_cli, worker_env, fake_service, capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["worker", "--data-dir", str(worker_env.data), "--workers", "dois", "--min-free-memory", "0"])
    assert info.value.code == 2
    err = capsys.readouterr().err
    assert "erro:" in err and "deve ser um número inteiro" in err and "argumento --workers" in err


def test_worker_rejects_a_missing_data_dir(run_cli, worker_env, fake_service):
    code, _out, err = run_cli("worker", "--data-dir", worker_env.tmp / "nao-existe", "--workers", 1,
                              "--min-free-memory", 0)
    assert code == 2 and "A pasta de dados não existe" in err


def test_worker_without_the_queue_extra_exits_3(run_cli, worker_env, monkeypatch):
    monkeypatch.setitem(sys.modules, "pika", None)  # `import pika` passa a levantar ImportError
    code, _out, err = run_cli(*_worker_args(worker_env))
    assert code == 3
    assert "O worker precisa das dependências da fila. Instale com: uv sync --extra queue" in err


def test_worker_without_credentials_exits_4_and_never_prints_a_value(run_cli, worker_env, fake_service):
    code, _out, err = run_cli(*_worker_args(worker_env))
    assert code == 4
    assert ("Credenciais do RabbitMQ não encontradas. Defina RABBITMQ_DEFAULT_USER e RABBITMQ_DEFAULT_PASS "
            "no ambiente ou em stack/.env.") in err


def test_worker_passes_the_options_to_the_service_and_hides_the_credentials(run_cli, worker_env, fake_service):
    worker_env.monkeypatch.setenv("RABBITMQ_DEFAULT_USER", "usuario-teste")
    worker_env.monkeypatch.setenv("RABBITMQ_DEFAULT_PASS", "senha-teste-secreta")
    code, _out, err = run_cli(*_worker_args(worker_env, "--retry-delay", "5", "--max-attempts", "4",
                                            "--idle-timeout", "0", "--shutdown-timeout", "7",
                                            "--conversion-timeout", "60"))
    assert code == 0
    (settings,) = fake_service.settings
    assert (settings.workers, settings.min_free_memory_mb, settings.retry_delay, settings.max_attempts,
            settings.idle_timeout, settings.shutdown_timeout, settings.conversion_timeout) == (2, 500, 5, 4, 0, 7, 60)
    assert settings.data_dir == worker_env.data.resolve() and settings.host == "127.0.0.1"
    assert "senha-teste-secreta" not in err and "usuario-teste" not in err
    assert "RABBITMQ_DEFAULT_PASS" not in os.environ  # removida do ambiente antes de qualquer filho


@pytest.mark.parametrize("service_code", [0, 4, 5])
def test_worker_returns_the_exit_code_of_the_service(run_cli, worker_env, fake_service, service_code):
    worker_env.monkeypatch.setenv("RABBITMQ_DEFAULT_USER", "u")
    worker_env.monkeypatch.setenv("RABBITMQ_DEFAULT_PASS", "p")
    fake_service.exit_code = service_code
    assert run_cli(*_worker_args(worker_env))[0] == service_code


def test_worker_help_documents_the_options_in_portuguese(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["worker", "--help"])
    assert info.value.code == 0
    out = capsys.readouterr().out
    for option in ("--data-dir", "--workers", "--min-free-memory", "--retry-delay", "--max-attempts",
                   "--idle-timeout", "--shutdown-timeout", "--conversion-timeout"):
        assert option in out
    from artigo2md.jobqueue import settings as queue_settings

    assert f"padrão: {queue_settings.DEFAULT_WORKERS}" in out
    assert f"padrão: {queue_settings.DEFAULT_MIN_FREE_MEMORY_MB}" in out
    assert "docs/specs/conversion-queue/ram-measurement.md" in out


# ── a fila não é carregada por convert/doctor/serve; o worker não importa os motores (5.3.5, 5.3.6) ──
def _run_isolated(code: str, env_extra: dict | None = None) -> str:
    env = {**os.environ, **(env_extra or {})}
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True, env=env)
    return done.stdout


def test_convert_doctor_and_serve_do_not_load_the_queue(tmp_path):
    code = f"""
import json, sys
from artigo2md import cli
cli.main(["doctor"])
cli.cmd_serve = lambda args: 0
out = {{}}
cli.main(["serve", "--port", "1"])
print(json.dumps({{"pika": "pika" in sys.modules, "jobqueue": any(m.startswith("artigo2md.jobqueue") for m in sys.modules)}}))
"""
    loaded = json.loads(_run_isolated(code).strip().splitlines()[-1])
    assert loaded == {"pika": False, "jobqueue": False}


def test_convert_output_does_not_depend_on_the_queue(tmp_path, article_pdf):
    code = f"""
import json, sys
from artigo2md import cli
status = cli.main(["convert", {str(article_pdf)!r}, "-o", {str(tmp_path / 'saida')!r}, "-e", "markitdown"])
print(json.dumps({{"status": status, "pika": "pika" in sys.modules,
                   "jobqueue": any(m.startswith("artigo2md.jobqueue") for m in sys.modules)}}))
"""
    loaded = json.loads(_run_isolated(code).strip().splitlines()[-1])
    assert loaded == {"status": 0, "pika": False, "jobqueue": False}
    assert (tmp_path / "saida" / "artigo.md").read_text(encoding="utf-8").strip()


def test_worker_main_process_does_not_import_the_conversion_engines(tmp_path):
    pytest.importorskip("pika")
    pytest.importorskip("psutil")
    (tmp_path / "dados").mkdir()
    code = f"""
import json, sys
from artigo2md import cli
from artigo2md.jobqueue import amqp

class ProbableAuthenticationError(Exception):
    pass

def refuse(_settings):
    raise ProbableAuthenticationError("recusado")

amqp.open_connection = refuse  # nenhuma conexão real: o teste só quer o caminho de código do worker
status = cli.main(["worker", "--data-dir", {str(tmp_path / 'dados')!r}, "--workers", "1", "--min-free-memory", "0"])
print(json.dumps({{"status": status, "docling": "docling" in sys.modules,
                   "pymupdf4llm": "pymupdf4llm" in sys.modules, "pika": "pika" in sys.modules}}))
"""
    env = {"RABBITMQ_DEFAULT_USER": "u", "RABBITMQ_DEFAULT_PASS": "p"}
    loaded = json.loads(_run_isolated(code, env).strip().splitlines()[-1])
    assert loaded["status"] == 4
    assert loaded["docling"] is False and loaded["pymupdf4llm"] is False
