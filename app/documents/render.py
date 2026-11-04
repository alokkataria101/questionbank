"""Markdown / PDF / DOCX output for one question or a whole exam paper.

A "paper item" is a dict: {number, text, marks, diagram (object model), code}.
"""
from __future__ import annotations

import io
from pathlib import Path

from .. import diagram as dg
from . import latex


# ── Markdown ─────────────────────────────────────────────────────────────────
def to_markdown(header: dict, items: list[dict], asset_dir: str = "assets") -> tuple[str, dict[str, bytes]]:
    """Returns (markdown, {relative asset path: bytes}). Diagrams are saved as SVG next to the .md."""
    assets: dict[str, bytes] = {}
    lines = [f"# {header.get('school', '')}", ""]
    if header.get("title"):
        lines += [f"## {header['title']}", ""]
    meta = [f"**Class:** {header['class_name']}" if header.get("class_name") else "",
            f"**Subject:** {header['subject']}" if header.get("subject") else "",
            f"**Time:** {header['duration']}" if header.get("duration") else "",
            f"**Maximum Marks:** {header['total_marks']}" if header.get("total_marks") is not None else ""]
    lines += [" &nbsp;|&nbsp; ".join(m for m in meta if m), "", "---", ""]
    for it in items:
        lines.append(f"**Q{it['number']}.** {it['text'].strip()}")
        lines.append("")
        if it.get("diagram", {}).get("objects"):
            rel = f"{asset_dir}/q{it['number']}.svg"
            assets[rel] = dg.to_svg(it["diagram"]).encode()
            lines += [f"![Diagram for Q{it['number']}]({rel})", ""]
        lines += [f"*[{it['marks']} Mark{'s' if it['marks'] != 1 else ''}]*", "", "---", ""]
    return "\n".join(lines), assets


def _meta(header: dict) -> list[str]:
    """Header fields that have a value; blank ones are left out."""
    pairs = [("Class", header.get("class_name")), ("Subject", header.get("subject")),
             ("Time", header.get("duration")), ("Maximum Marks", header.get("total_marks"))]
    return [f"{k}: {v}" for k, v in pairs if v not in (None, "")]


# ── PDF ──────────────────────────────────────────────────────────────────────
def _pdf_fonts() -> tuple[str, str]:
    """Register a Unicode TTF (Greek, ×, θ, ∠ ...). Falls back to Helvetica."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    mac = "/System/Library/Fonts/Supplemental/"
    dj = "/usr/share/fonts/truetype/dejavu/"
    candidates = [  # (regular, bold, italic, bold-italic)
        (mac + "Times New Roman.ttf", mac + "Times New Roman Bold.ttf",
         mac + "Times New Roman Italic.ttf", mac + "Times New Roman Bold Italic.ttf"),
        (mac + "Arial Unicode.ttf", None, None, None),
        (dj + "DejaVuSerif.ttf", dj + "DejaVuSerif-Bold.ttf", dj + "DejaVuSerif-Italic.ttf",
         dj + "DejaVuSerif-BoldItalic.ttf"),
    ]
    for reg, bold, ital, bital in candidates:
        if Path(reg).is_file():
            try:
                def face(name, path):
                    p = path if path and Path(path).is_file() else reg
                    pdfmetrics.registerFont(TTFont(name, p))
                face("QBBody", reg); face("QBBold", bold); face("QBItalic", ital); face("QBBoldItalic", bital)
                pdfmetrics.registerFontFamily("QBBody", normal="QBBody", bold="QBBold",
                                              italic="QBItalic", boldItalic="QBBoldItalic")
                return "QBBody", "QBBold"
            except Exception:
                continue
    return "Helvetica", "Helvetica-Bold"


def to_pdf(header: dict, items: list[dict], logo: bytes | None = None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (HRFlowable, Image, KeepTogether, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)

    body_font, bold_font = _pdf_fonts()
    body = ParagraphStyle("b", fontName=body_font, fontSize=11.5, leading=17)
    school = ParagraphStyle("s", fontName=bold_font, fontSize=17, leading=22, alignment=TA_CENTER)
    title = ParagraphStyle("t", fontName=bold_font, fontSize=13, leading=18, alignment=TA_CENTER)
    marks = ParagraphStyle("m", fontName=bold_font, fontSize=10.5, alignment=TA_RIGHT)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm,
                            title=header.get("title") or "Question", author=header.get("school", ""))
    flow: list = []
    if logo:
        flow.append(Image(io.BytesIO(logo), width=18 * mm, height=18 * mm, kind="proportional"))
    flow.append(Paragraph(latex.runs_to_markup([(header.get("school", ""), "")]), school))
    if header.get("title"):
        flow.append(Paragraph(latex.runs_to_markup([(header["title"], "")]), title))
    meta_cells = _meta(header)
    meta_cells += [""] * (len(meta_cells) % 2)
    t = Table([[Paragraph(meta_cells[i], body), Paragraph(meta_cells[i + 1], body)]
               for i in range(0, len(meta_cells), 2)] or [[""]], colWidths=[85 * mm, 85 * mm])
    t.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    flow += [Spacer(1, 4 * mm), t, HRFlowable(width="100%", thickness=0.8, color=colors.black, spaceBefore=3,
                                              spaceAfter=8)]

    for it in items:
        block: list = []
        paragraphs = [p for p in it["text"].strip().split("\n\n") if p.strip()] or [""]
        for n, para in enumerate(paragraphs):
            parts = latex.split_math(para)
            inline_buf: list[tuple[str, str]] = []

            def flush(prefix: str = "") -> None:
                if inline_buf or prefix:
                    runs = ([(prefix, "b")] if prefix else []) + inline_buf
                    markup = "".join(
                        f"<b>{latex.runs_to_markup([(t_, '')])}</b>" if k == "b" else latex.runs_to_markup([(t_, k)])
                        for t_, k in runs).replace("\n", "<br/>")
                    block.append(Paragraph(markup, ParagraphStyle("q", parent=body, leftIndent=12 * mm,
                                                                  firstLineIndent=-12 * mm if prefix else 0)
                                           if prefix else ParagraphStyle("q2", parent=body, leftIndent=12 * mm)))
                    inline_buf.clear()

            prefix = f"Q{it['number']}. " if n == 0 else ""
            for kind, content in parts:
                if kind == "display":
                    flush(prefix); prefix = ""
                    png = latex.render_display(content)
                    if png:
                        from PIL import Image as PILImage
                        w, h = PILImage.open(io.BytesIO(png)).size
                        sc = min(1.0, (150 * mm) / (w * 72 / 200))
                        block.append(Image(io.BytesIO(png), width=w * 72 / 200 * sc, height=h * 72 / 200 * sc,
                                           hAlign="CENTER"))
                    else:
                        block.append(Paragraph(latex.runs_to_markup(
                            [(t_, "i" if k == "" else k) for t_, k in latex.tex_runs(content)]),
                            ParagraphStyle("d", parent=body, alignment=TA_CENTER)))
                elif kind == "inline":
                    inline_buf += [(t_, "i" if k == "" else k) for t_, k in latex.tex_runs(content)]
                else:
                    inline_buf.append((content, ""))
            flush(prefix)
            block.append(Spacer(1, 2 * mm))
        if it.get("diagram", {}).get("objects"):
            d = it["diagram"]
            png = dg.to_png(d)
            w_pt = min(110 * mm, d.get("width", 400) * 0.75)
            block.append(Image(io.BytesIO(png), width=w_pt, height=w_pt * d.get("height", 300) / d.get("width", 400),
                               hAlign="CENTER"))
        block.append(Paragraph(f"[{it['marks']} Mark{'s' if it['marks'] != 1 else ''}]", marks))
        block.append(HRFlowable(width="100%", thickness=0.3, color=colors.grey, spaceBefore=4, spaceAfter=8))
        flow.append(KeepTogether(block))
    doc.build(flow)
    return buf.getvalue()


# ── DOCX ─────────────────────────────────────────────────────────────────────
def to_docx(header: dict, items: list[dict], logo: bytes | None = None) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Mm, Pt

    d = Document()
    d.styles["Normal"].font.name = "Times New Roman"
    d.styles["Normal"].font.size = Pt(12)
    for s in d.sections:
        s.left_margin = s.right_margin = Mm(20)
    if logo:
        d.add_picture(io.BytesIO(logo), width=Mm(18))
        d.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = d.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(header.get("school", "")); r.bold = True; r.font.size = Pt(18)
    if header.get("title"):
        p = d.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(header["title"]); r.bold = True; r.font.size = Pt(13)
    meta = _meta(header)
    for i in range(0, len(meta), 2):
        d.add_paragraph("\t\t".join(meta[i:i + 2]))
    d.add_paragraph("_" * 78)

    def add_runs(par, runs, bold=False):
        for text, k in runs:
            r = par.add_run(text)
            r.bold = bold
            r.italic = k == "i"
            r.font.subscript = k == "sub"
            r.font.superscript = k == "sup"

    for it in items:
        paragraphs = [p_ for p_ in it["text"].strip().split("\n\n") if p_.strip()] or [""]
        for n, para in enumerate(paragraphs):
            par = d.add_paragraph()
            par.paragraph_format.left_indent = Mm(12)
            if n == 0:
                par.paragraph_format.first_line_indent = Mm(-12)
                add_runs(par, [(f"Q{it['number']}. ", "")], bold=True)
            for kind, content in latex.split_math(para):
                if kind == "display":
                    png = latex.render_display(content)
                    if png:
                        par = d.add_paragraph(); par.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        par.add_run().add_picture(io.BytesIO(png), height=Mm(9))
                        par = d.add_paragraph(); par.paragraph_format.left_indent = Mm(12)
                    else:
                        add_runs(par, [(t, "i" if k == "" else k) for t, k in latex.tex_runs(content)])
                elif kind == "inline":
                    add_runs(par, [(t, "i" if k == "" else k) for t, k in latex.tex_runs(content)])
                else:
                    add_runs(par, [(content, "")])
        if it.get("diagram", {}).get("objects"):
            dd = it["diagram"]
            pic = d.add_paragraph(); pic.alignment = WD_ALIGN_PARAGRAPH.CENTER
            pic.add_run().add_picture(io.BytesIO(dg.to_png(dd)), width=Mm(90))
        m = d.add_paragraph(); m.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        add_runs(m, [(f"[{it['marks']} Mark{'s' if it['marks'] != 1 else ''}]", "")], bold=True)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()
