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
def blank_pdf(tmp_path: Path) -> Path:
    pdf = FPDF()
    pdf.add_page()
    pdf.output(str(tmp_path / "branco.pdf"))
    return tmp_path / "branco.pdf"
