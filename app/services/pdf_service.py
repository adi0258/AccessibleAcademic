import json
import os
from pathlib import Path

from bidi.algorithm import get_display
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fpdf import FPDF
from sqlmodel import Session

from app.core.config import EXPORTS_DIR, FONT_FILE
from app.models import Lecture
from app.services.math_utils import latex_to_text


PDF_FONT_NAME = "AccessibleAcademicUnicode"


def _load_processed_content(lecture: Lecture) -> dict:
    try:
        return json.loads(lecture.processed_content_json)
    except Exception:
        return {"topics": [], "summaries": [], "flashcards": []}


def _safe_title(value: str, fallback: str) -> str:
    safe_title = "".join(c for c in value if c not in r"\/:*?\"<>|").strip() or fallback
    return safe_title.replace("\n", " ").replace("\r", " ")[:200]


def _candidate_font_paths() -> list[Path]:
    candidates: list[Path] = []

    env_font = os.getenv("PDF_FONT_PATH")
    if env_font:
        candidates.append(Path(env_font))

    candidates.extend(
        [
            FONT_FILE,
            Path(r"C:\Windows\Fonts\segoeui.ttf"),
            Path(r"C:\Windows\Fonts\arial.ttf"),
            Path(r"C:\Windows\Fonts\calibri.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSansHebrew-Regular.ttf"),
            Path("/Library/Fonts/Arial.ttf"),
            Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        ]
    )

    unique_candidates: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate_str = str(candidate)
        if candidate_str in seen:
            continue
        seen.add(candidate_str)
        if candidate.exists():
            unique_candidates.append(candidate)
    return unique_candidates


def _load_unicode_font(pdf: FPDF) -> str:
    for font_path in _candidate_font_paths():
        try:
            pdf.add_font(PDF_FONT_NAME, "", str(font_path), uni=True)
            return PDF_FONT_NAME
        except Exception:
            continue

    raise HTTPException(
        status_code=500,
        detail=(
            "PDF export requires a Unicode TTF font. Add one under assets/fonts/, "
            "or set PDF_FONT_PATH to a Hebrew-capable .ttf file."
        ),
    )


def export_lecture_pdf(lecture_id: int, session: Session):
    lecture = session.get(Lecture, lecture_id)
    if not lecture or lecture.status != "completed":
        raise HTTPException(status_code=404, detail="Lecture not ready")

    data = _load_processed_content(lecture)

    pdf = FPDF()
    pdf.set_margins(20, 20, 20)
    pdf.add_page()
    usable_width = pdf.w - pdf.l_margin - pdf.r_margin

    font_name = _load_unicode_font(pdf)

    def write_rtl_multiline(text, font_size):
        text = latex_to_text(text)
        pdf.set_font(font_name, "", font_size)
        lines = pdf.multi_cell(usable_width, 8, txt=text, align="R", split_only=True)
        for line in lines:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(usable_width, 8, txt=get_display(line), align="R")

    def add_section_header(text):
        pdf.ln(5)
        pdf.set_font(font_name, "", 18)
        pdf.set_text_color(0, 51, 102)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(usable_width, 10, txt=get_display(text), align="R")
        pdf.set_draw_color(0, 51, 102)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(5)
        pdf.set_text_color(0, 0, 0)

    write_rtl_multiline(f"סיכום הרצאה: {lecture.title}", 22)
    pdf.ln(10)

    if data.get("topics"):
        add_section_header("נושאים מרכזיים")
        for topic in data["topics"]:
            write_rtl_multiline(f"• {topic}", 13)
        pdf.ln(5)

    if data.get("summaries"):
        add_section_header("סיכום מורחב")
        for item in data["summaries"]:
            topic_title = item.get("topic_name", "נושא")
            content = item.get("content", "")
            write_rtl_multiline(f"{topic_title}:", 14)
            write_rtl_multiline(content, 12)
            pdf.ln(2)
            pdf.set_draw_color(220, 220, 220)
            pdf.line(pdf.l_margin + 20, pdf.get_y(), pdf.w - pdf.r_margin - 20, pdf.get_y())
            pdf.ln(4)

    if data.get("flashcards"):
        pdf.add_page()
        add_section_header("כרטיסיות זיכרון")
        for i, card in enumerate(data["flashcards"], 1):
            write_rtl_multiline(f"{i}. שאלה: {card.get('question')}", 13)
            write_rtl_multiline(f"תשובה: {card.get('answer')}", 12)
            pdf.ln(6)

    safe_title = _safe_title(lecture.title, f"lecture-{lecture_id}")
    download_filename = f"summery-{safe_title}.pdf"

    export_path = EXPORTS_DIR / f"export_{lecture_id}.pdf"
    pdf.output(str(export_path))
    return FileResponse(export_path, filename=download_filename)
