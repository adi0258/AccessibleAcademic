import json
import os

from bidi.algorithm import get_display
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fpdf import FPDF
from sqlmodel import Session

from models.lecture import Lecture


def export_lecture_pdf(lecture_id: int, session: Session, base_dir: str):
    lecture = session.get(Lecture, lecture_id)
    if not lecture or lecture.status != "completed":
        raise HTTPException(status_code=404, detail="Lecture not ready")

    try:
        data = json.loads(lecture.processed_content_json)
    except Exception:
        data = {"topics": [], "summaries": [], "flashcards": []}

    pdf = FPDF()
    pdf.set_margins(20, 20, 20)
    pdf.add_page()
    usable_width = pdf.w - pdf.l_margin - pdf.r_margin

    font_name = "Arial"
    font_path = os.path.join(base_dir, "Heebo-VariableFont_wght.ttf")
    if os.path.exists(font_path):
        try:
            pdf.add_font("Heebo", "", font_path, uni=True)
            font_name = "Heebo"
        except Exception:
            pass

    def write_rtl_multiline(text, font_size):
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

    safe_title = "".join(c for c in lecture.title if c not in r"\/:*?\"<>|").strip() or f"lecture-{lecture_id}"
    safe_title = safe_title.replace("\n", " ").replace("\r", " ")[:200]
    download_filename = f"summery-{safe_title}.pdf"

    export_path = os.path.join(base_dir, f"export_{lecture_id}.pdf")
    pdf.output(export_path)
    return FileResponse(export_path, filename=download_filename)
