"""Build the Word manual (docs/YORIQNOMA-face-date.docx) from docs/yoriqnoma_uz.md.

The Markdown file is the single source of truth: it is easy to edit and diff,
this script turns it into a styled .docx (A4, headers, code blocks, tables).
Run from the project root:

    .venv\\Scripts\\python.exe scripts\\build_manual.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "yoriqnoma_uz.md"
TARGET = ROOT / "docs" / "YORIQNOMA-face-date.docx"

FONT = "Calibri"
MONO = "Consolas"
ACCENT = RGBColor(0x1F, 0x38, 0x64)
ACCENT2 = RGBColor(0x2E, 0x54, 0x96)
MUTED = RGBColor(0x59, 0x59, 0x59)

INLINE = re.compile(r"(\*\*.+?\*\*|`[^`]+`)")


# --------------------------------------------------------------------------- #
#  Low level helpers
# --------------------------------------------------------------------------- #
# Schema order matters: these tags must stay *after* w:shd / w:pBdr inside
# w:pPr, otherwise Word reports the file as damaged.
_AFTER_SHD = (
    "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap", "w:overflowPunct",
    "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi", "w:adjustRightInd",
    "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing", "w:mirrorIndents",
    "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment",
    "w:textboxTightWrap", "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr",
    "w:sectPr", "w:pPrChange",
)
_AFTER_TC_SHD = (
    "w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign",
    "w:hideMark", "w:headers", "w:cellIns", "w:cellDel", "w:cellMerge",
    "w:tcPrChange",
)


def _shd(color: str):
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color)
    return shd


def shade(paragraph, color: str) -> None:
    """Apply a solid background to a paragraph."""
    p_pr = paragraph._p.get_or_add_pPr()
    p_pr.insert_element_before(_shd(color), *_AFTER_SHD)


def left_bar(paragraph, color: str) -> None:
    """Thin vertical rule on the left side of a paragraph."""
    p_pr = paragraph._p.get_or_add_pPr()
    border = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), "12")
    left.set(qn("w:space"), "4")
    left.set(qn("w:color"), color)
    border.append(left)
    p_pr.insert_element_before(border, "w:shd", *_AFTER_SHD)


def cell_shade(cell, color: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_pr.insert_element_before(_shd(color), *_AFTER_TC_SHD)


def field(paragraph, code: str, placeholder: str = "1") -> None:
    """Insert a Word field (PAGE, NUMPAGES ...) into *paragraph*."""
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" {code} "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = placeholder
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for node in (begin, instr, separate, text, end):
        run._r.append(node)


def add_runs(paragraph, text: str, size: float = 10.5, color=None) -> None:
    """Write *text* with **bold**, *italic* and `code` inline markup.

    Code spans are resolved first so that underscores/asterisks inside them
    (``PERSON_*``, ``os.environ[...]``) never start an emphasis run.
    """
    for code_index, code_part in enumerate(text.split("`")):
        if not code_part:
            continue
        if code_index % 2 == 1:  # inside `backticks`
            run = paragraph.add_run(code_part)
            run.font.name = MONO
            run._element.rPr.rFonts.set(qn("w:eastAsia"), MONO)
            run.font.size = Pt(size - 1.2)
            run.font.color.rgb = RGBColor(0xA3, 0x1D, 0x1D)
            continue

        for bold_index, bold_part in enumerate(code_part.split("**")):
            if not bold_part:
                continue
            for italic_index, part in enumerate(bold_part.split("*")):
                if not part:
                    continue
                run = paragraph.add_run(part)
                run.font.size = Pt(size)
                if color is not None:
                    run.font.color.rgb = color
                if bold_index % 2 == 1:
                    run.bold = True
                if italic_index % 2 == 1:
                    run.italic = True


# --------------------------------------------------------------------------- #
#  Document skeleton
# --------------------------------------------------------------------------- #
def new_document() -> Document:
    doc = Document()

    section = doc.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.0)
    section.bottom_margin = Cm(1.8)
    section.left_margin = Cm(2.2)
    section.right_margin = Cm(2.0)
    section.header_distance = Cm(1.0)
    section.footer_distance = Cm(1.0)

    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(10.5)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    normal.paragraph_format.space_after = Pt(5)
    normal.paragraph_format.line_spacing = 1.12

    for name, size, color, before, after in (
        ("Heading 1", 15, ACCENT, 16, 6),
        ("Heading 2", 12.5, ACCENT2, 12, 4),
        ("Heading 3", 11, RGBColor(0x44, 0x54, 0x6A), 8, 3),
    ):
        style = doc.styles[name]
        style.font.name = FONT
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = color
        style.font.italic = False
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True
        style.paragraph_format.line_spacing = 1.0

    code = doc.styles.add_style("CodeBlock", 1)  # 1 = paragraph
    code.font.name = MONO
    code.font.size = Pt(8.5)
    code.element.rPr.rFonts.set(qn("w:eastAsia"), MONO)
    code.paragraph_format.space_after = Pt(0)
    code.paragraph_format.space_before = Pt(0)
    code.paragraph_format.line_spacing = 1.0
    code.paragraph_format.left_indent = Cm(0.35)
    code.paragraph_format.right_indent = Cm(0.15)

    # header / footer
    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    add_runs(header, "face-date  ·  AI CCTV: xodimlarni tanish va jihozlar inventarizatsiyasi", 8, MUTED)

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run("Sahifa ")
    run.font.size = Pt(8)
    run.font.color.rgb = MUTED
    field(footer, "PAGE")
    run = footer.add_run(" / ")
    run.font.size = Pt(8)
    run.font.color.rgb = MUTED
    field(footer, "NUMPAGES")

    return doc


def add_cover(doc: Document, title: str) -> None:
    for _ in range(5):
        doc.add_paragraph()

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("AI CCTV")
    run.font.name = FONT
    run.font.size = Pt(34)
    run.font.bold = True
    run.font.color.rgb = ACCENT

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(title)
    run.font.name = FONT
    run.font.size = Pt(18)
    run.font.bold = True
    run.font.color.rgb = ACCENT2

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("T O ‘ L I Q   F O Y D A L A N U V C H I   Q O ‘ L L A N M A S I")
    run.font.size = Pt(9.5)
    run.font.color.rgb = MUTED

    for _ in range(7):
        doc.add_paragraph()

    info = doc.add_table(rows=0, cols=2)
    info.style = "Table Grid"
    info.alignment = WD_TABLE_ALIGNMENT.CENTER
    rows = (
        ("Loyiha", "face-date · xodimlarni tanish + jihozlar inventarizatsiyasi"),
        ("Versiya", "1.0"),
        ("Sana", "2026-yil 28-oktabr"),
        ("Platforma", "Windows 22H2 · Python 3.11 · FastAPI"),
        ("Veb-manzil", "http://127.0.0.1:8000"),
        ("Hujjat tili", "o‘zbek (lotin)"),
    )
    for label, value in rows:
        cells = info.add_row().cells
        p = cells[0].paragraphs[0]
        run = p.add_run(label)
        run.bold = True
        run.font.size = Pt(10)
        cell_shade(cells[0], "EDF1F8")
        p = cells[1].paragraphs[0]
        run = p.add_run(value)
        run.font.size = Pt(10)
    info.columns[0].width = Cm(4.5)
    info.columns[1].width = Cm(11.5)
    for row in info.rows:
        row.cells[0].width = Cm(4.5)
        row.cells[1].width = Cm(11.5)

    for _ in range(6):
        doc.add_paragraph()

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(
        "Ushbu hujjat tizimni nolldan ishga tushirish, har kuni ishlatish, "
        "texniklarni rasm bilan o‘qitish, kamera va chiziqni sozlash, "
        "inventarni yuritish va nosozliklarni bartaraf etish uchun yozilgan."
    )
    run.font.size = Pt(9.5)
    run.font.color.rgb = MUTED
    run.italic = True
    p.paragraph_format.left_indent = Cm(2.0)
    p.paragraph_format.right_indent = Cm(2.0)

    doc.add_page_break()


# --------------------------------------------------------------------------- #
#  Block renderers
# --------------------------------------------------------------------------- #
def add_table(doc: Document, lines: list[str]) -> None:
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines]
    rows = [row for row in rows if not all(re.fullmatch(r":?-{2,}:?", cell or "-") for cell in row)]
    if not rows:
        return

    width = len(rows[0])
    table = doc.add_table(rows=0, cols=width)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True

    for index, row in enumerate(rows):
        cells = table.add_row().cells
        for position in range(width):
            text = row[position] if position < len(row) else ""
            paragraph = cells[position].paragraphs[0]
            paragraph.paragraph_format.space_after = Pt(1)
            paragraph.paragraph_format.line_spacing = 1.0
            add_runs(paragraph, text, 9 if index else 9.2)
            if index == 0:
                cell_shade(cells[position], "2E5496")
                for run in paragraph.runs:
                    run.bold = True
                    run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            elif index % 2 == 0:
                cell_shade(cells[position], "F5F7FB")

    # repeat the header on every page
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    table.rows[0]._tr.get_or_add_trPr().append(tbl_header)

    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def add_code(doc: Document, lines: list[str], language: str = "") -> None:
    if language == "text" and lines and lines[0].startswith(("Kamera", "face-date", "C:\\")):
        pass
    paragraph = doc.add_paragraph(style="CodeBlock")
    paragraph.paragraph_format.keep_together = True
    for index, line in enumerate(lines):
        run = paragraph.add_run(line)
        run.font.name = MONO
        run.font.size = Pt(8.5)
        if index < len(lines) - 1:
            run.add_break(WD_BREAK.LINE)
    shade(paragraph, "F4F6F8")
    left_bar(paragraph, "9DB4D6")

    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(3)
    spacer.paragraph_format.line_spacing = 1.0
    spacer_run = spacer.add_run(" ")
    spacer_run.font.size = Pt(4)


def add_quote(doc: Document, lines: list[str]) -> None:
    text = " ".join(part.strip("> ").strip() for part in lines)
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.left_indent = Cm(0.6)
    paragraph.paragraph_format.space_before = Pt(4)
    paragraph.paragraph_format.space_after = Pt(6)
    add_runs(paragraph, text, 9.5, MUTED)
    for run in paragraph.runs:
        run.italic = True
    shade(paragraph, "FBF7E8")
    left_bar(paragraph, "D9C77A")


def add_bullet(doc: Document, text: str, level: int = 0) -> None:
    if text.startswith("[ ] "):
        paragraph = doc.add_paragraph()
        paragraph.paragraph_format.left_indent = Cm(0.5)
        paragraph.paragraph_format.first_line_indent = Cm(-0.5)
        paragraph.paragraph_format.space_after = Pt(1)
        run = paragraph.add_run("☐  ")
        run.font.size = Pt(10)
        add_runs(paragraph, text[4:])
        return
    style = "List Bullet 2" if level else "List Bullet"
    try:
        paragraph = doc.add_paragraph(style=style)
    except KeyError:  # style table differs between Word versions
        paragraph = doc.add_paragraph(style="List Bullet")
    paragraph.paragraph_format.space_after = Pt(1.5)
    paragraph.paragraph_format.line_spacing = 1.08
    add_runs(paragraph, text)


def add_numbered(doc: Document, number: str, text: str) -> None:
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.left_indent = Cm(0.7)
    paragraph.paragraph_format.first_line_indent = Cm(-0.7)
    paragraph.paragraph_format.space_after = Pt(1.5)
    run = paragraph.add_run(f"{number}  ")
    run.bold = True
    run.font.size = Pt(10.5)
    run.font.color.rgb = ACCENT2
    add_runs(paragraph, text)


# --------------------------------------------------------------------------- #
#  Markdown driver
# --------------------------------------------------------------------------- #
def build(doc: Document, markdown: str) -> None:
    lines = markdown.splitlines()
    index = 0
    first_heading = True
    after_break = True
    in_cover = True  # everything before the first --- is drawn on the cover page

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if in_cover:
            if stripped == "---":
                in_cover = False
            index += 1
            continue

        if not stripped:
            index += 1
            continue

        # fenced code
        if stripped.startswith("```"):
            language = stripped[3:].strip()
            block: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                block.append(lines[index].rstrip("\n"))
                index += 1
            index += 1
            add_code(doc, block, language)
            after_break = False
            continue

        # page break between top level sections
        if stripped == "---":
            if not after_break:
                doc.add_page_break()
                after_break = True
            index += 1
            continue

        # headings
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            title = stripped[level:].strip()
            if level == 1 and first_heading:
                first_heading = False
                index += 1
                continue  # the cover page already renders the title
            clean = title.replace("**", "").replace("`", "")
            doc.add_heading(clean, min(level, 3))
            after_break = False
            index += 1
            continue

        # tables
        if stripped.startswith("|"):
            block = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                block.append(lines[index])
                index += 1
            add_table(doc, block)
            after_break = False
            continue

        # quotes
        if stripped.startswith(">"):
            block = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                block.append(lines[index])
                index += 1
            add_quote(doc, block)
            after_break = False
            continue

        # bullets
        bullet = re.match(r"^(\s*)[-*]\s+(.*)$", line)
        if bullet:
            level = 1 if len(bullet.group(1)) >= 2 else 0
            add_bullet(doc, bullet.group(2), level)
            after_break = False
            index += 1
            continue

        # numbered list (manual numbering keeps each list restarting at 1)
        number = re.match(r"^(\s*)(\d+[.)])\s+(.*)$", line)
        if number:
            add_numbered(doc, number.group(2), number.group(3))
            after_break = False
            index += 1
            continue

        # plain paragraph (join wrapped lines)
        block = [stripped]
        index += 1
        while index < len(lines):
            nxt = lines[index]
            if (
                not nxt.strip()
                or nxt.strip().startswith(("#", "|", ">", "```", "---"))
                or re.match(r"^\s*[-*]\s+", nxt)
                or re.match(r"^\s*\d+[.)]\s+", nxt)
            ):
                break
            block.append(nxt.strip())
            index += 1
        paragraph = doc.add_paragraph()
        add_runs(paragraph, " ".join(block))
        after_break = False


def main() -> int:
    if not SOURCE.exists():
        print(f"Manba topilmadi: {SOURCE}")
        return 1

    markdown = SOURCE.read_text(encoding="utf-8")
    title = "Xodimlarni tanish va jihozlar inventarizatsiyasi"
    for line in markdown.splitlines():
        if line.startswith("# "):
            title = line[2:].strip().split(":", 1)[-1].strip() or title
            break

    doc = new_document()
    add_cover(doc, title)
    build(doc, markdown)

    props = doc.core_properties
    props.title = "AI CCTV — to‘liq foydalanuvchi qo‘llanmasi"
    props.subject = "Xodimlarni tanish va jihozlar inventarizatsiyasi"
    props.author = "face-date"
    props.comments = "docs/yoriqnoma_uz.md dan avtomatik yaratilgan"
    props.language = "uz-UZ"

    doc.save(TARGET)
    size = TARGET.stat().st_size / 1024
    print(f"Tayyor: {TARGET}  ({size:.0f} KB)")
    print(f"Paragraflar: {len(doc.paragraphs)}, jadvallar: {len(doc.tables)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
