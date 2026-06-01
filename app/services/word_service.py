import json

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlmodel import Session

from app.core.config import EXPORTS_DIR
from app.models import Lecture
from app.services.math_utils import latex_to_text


def _load_processed_content(lecture: Lecture) -> dict:
    try:
        return json.loads(lecture.processed_content_json)
    except Exception:
        return {"topics": [], "summaries": [], "flashcards": []}


def _safe_title(value: str, fallback: str) -> str:
    safe_title = "".join(c for c in value if c not in r"\/:*?\"<>|").strip() or fallback
    return safe_title.replace("\n", " ").replace("\r", " ")[:200]


def _set_paragraph_rtl(paragraph) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    bidi = p_pr.find(qn("w:bidi"))
    if bidi is None:
        bidi = OxmlElement("w:bidi")
        p_pr.append(bidi)
    bidi.set(qn("w:val"), "1")


def _set_run_rtl(run) -> None:
    r_pr = run._element.get_or_add_rPr()
    rtl = r_pr.find(qn("w:rtl"))
    if rtl is None:
        rtl = OxmlElement("w:rtl")
        r_pr.append(rtl)
    rtl.set(qn("w:val"), "1")

    r_fonts = r_pr.find(qn("w:rFonts"))
    if r_fonts is None:
        r_fonts = OxmlElement("w:rFonts")
        r_pr.append(r_fonts)
    for attr in ("ascii", "hAnsi", "cs"):
        r_fonts.set(qn(f"w:{attr}"), "Arial")


def _add_rtl_paragraph(document: Document, text: str, *, font_size: int, bold: bool = False) -> None:
    text = latex_to_text(text)
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _set_paragraph_rtl(paragraph)

    run = paragraph.add_run(text)
    run.bold = bold
    run.font.size = Pt(font_size)
    run.font.name = "Arial"
    _set_run_rtl(run)


def export_lecture_docx(lecture_id: int, session: Session):
    lecture = session.get(Lecture, lecture_id)
    if not lecture or lecture.status != "completed":
        raise HTTPException(status_code=404, detail="Lecture not ready")

    data = _load_processed_content(lecture)

    document = Document()
    document.core_properties.title = lecture.title

    _add_rtl_paragraph(document, f"סיכום הרצאה: {lecture.title}", font_size=20, bold=True)

    if data.get("topics"):
        _add_rtl_paragraph(document, "נושאים מרכזיים", font_size=16, bold=True)
        for topic in data["topics"]:
            _add_rtl_paragraph(document, f"• {topic}", font_size=12)

    if data.get("summaries"):
        _add_rtl_paragraph(document, "סיכום מורחב", font_size=16, bold=True)
        for item in data["summaries"]:
            topic_title = item.get("topic_name", "נושא")
            content = item.get("content", "")
            _add_rtl_paragraph(document, topic_title, font_size=13, bold=True)
            _add_rtl_paragraph(document, content, font_size=12)

    if data.get("flashcards"):
        _add_rtl_paragraph(document, "כרטיסיות זיכרון", font_size=16, bold=True)
        for index, card in enumerate(data["flashcards"], 1):
            _add_rtl_paragraph(document, f"{index}. שאלה: {card.get('question', '')}", font_size=12, bold=True)
            _add_rtl_paragraph(document, f"תשובה: {card.get('answer', '')}", font_size=12)

    safe_title = _safe_title(lecture.title, f"lecture-{lecture_id}")
    download_filename = f"summary-{safe_title}.docx"
    export_path = EXPORTS_DIR / f"export_{lecture_id}.docx"
    document.save(str(export_path))

    return FileResponse(
        export_path,
        filename=download_filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
