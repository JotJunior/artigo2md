import ipaddress
import os
import socket
from pathlib import Path

import pytest
from fpdf import FPDF

PARA = (
    "Background. Attention-deficit/hyperactivity disorder is a common neurodevelopmental "
    "condition. Methods. We conducted a randomized trial with 120 participants. Results. "
    "The intervention reduced symptoms compared with placebo. Discussion. These findings "
    "support the use of the intervention in adults. "
)


def _article(path: Path, with_table: bool = True) -> Path:
    pdf = FPDF()
    pdf.set_font("Helvetica", size=11)
    for page in range(2):
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 10, f"Section {page + 1}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=11)
        pdf.multi_cell(0, 6, PARA * 3)
    if with_table:
        pdf.ln(4)
        pdf.cell(0, 8, "Table 1. Baseline characteristics", new_x="LMARGIN", new_y="NEXT")
        with pdf.table() as table:
            for row in [("Group", "N", "Age"), ("Placebo", "60", "34.2"), ("Active", "60", "35.1")]:
                r = table.row()
                for cell in row:
                    r.cell(cell)
    pdf.output(str(path))
    return path


@pytest.fixture
def article_pdf(tmp_path: Path) -> Path:
    return _article(tmp_path / "artigo.pdf")


@pytest.fixture
def not_a_pdf(tmp_path: Path) -> Path:
    """Arquivo com extensão .pdf cujo conteúdo não é um PDF."""
    path = tmp_path / "nao-pdf.pdf"
    path.write_bytes(b"isto nao e pdf")
    return path


@pytest.fixture
def protected_pdf(tmp_path: Path) -> Path:
    """PDF cifrado: só abre com senha (as senhas abaixo são dados de teste)."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.cell(0, 10, "protected text")
    pdf.set_encryption(owner_password="owner-test", user_password="user-test")
    path = tmp_path / "protegido.pdf"
    pdf.output(str(path))
    return path


@pytest.fixture
def blank_pdf(tmp_path: Path) -> Path:
    pdf = FPDF()
    pdf.add_page()
    pdf.output(str(tmp_path / "branco.pdf"))
    return tmp_path / "branco.pdf"


@pytest.fixture
def formula_pdf(tmp_path: Path) -> Path:
    """PDF de texto cujo conteúdo usa um termo de indício de fórmula ("equation")."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.multi_cell(0, 6, "The dose follows the equation described in the appendix. " * 3)
    path = tmp_path / "formula.pdf"
    pdf.output(str(path))
    return path


@pytest.fixture
def no_network(monkeypatch):
    """Faz qualquer criação de socket em Python falhar durante o teste.

    Prova que o código testado não abre conexão pela biblioteca padrão; não enxerga
    acesso à rede feito por código nativo fora do Python.
    """

    def _blocked(*_args, **_kwargs):
        raise AssertionError("tentativa de acesso à rede durante o teste")

    monkeypatch.setattr(socket, "socket", _blocked)


# ── guarda de rede da suíte sem Docker (FR-019, FR-016; tarefa 2.3.5) ───────────────────────────────
def _is_loopback(address) -> bool:
    """`True` para endereços de loopback ou sockets de domínio Unix (caminho em `str`/`bytes`)."""
    if not isinstance(address, tuple) or not address:
        return True
    host = address[0]
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if host in ("", "localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False  # nome de host que não é "localhost": exigiria DNS e saída da máquina


@pytest.fixture(autouse=True)
def _no_outbound_network(request, monkeypatch):
    """Sem `ARTIGO2MD_STACK=1`, nenhum teste conecta fora de loopback; os testes da fila (`test_jobqueue_*`
    e `test_fake_amqp`) não conectam a nada: usam o corretor AMQP falso (`tests/fake_amqp.py`)."""
    if os.environ.get("ARTIGO2MD_STACK"):
        return
    queue_test = request.module.__name__.rsplit(".", 1)[-1].startswith(("test_jobqueue_", "test_fake_amqp"))
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def _allowed(address) -> bool:
        return False if queue_test else _is_loopback(address)

    def _connect(self, address):
        if not _allowed(address):
            raise AssertionError(f"tentativa de conexão de rede durante o teste: {address!r}")
        return real_connect(self, address)

    def _connect_ex(self, address):
        if not _allowed(address):
            raise AssertionError(f"tentativa de conexão de rede durante o teste: {address!r}")
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", _connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _connect_ex)
