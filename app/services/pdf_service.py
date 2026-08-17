import json
import os
import re
from pathlib import Path

from bidi.algorithm import get_display
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fpdf import FPDF
from sqlmodel import Session

from app.core.config import EXPORTS_DIR, FONT_FILE
from app.models import Lecture
from app.services.math_utils import clean_study_content, latex_to_text, _process_math

PDF_FONT_NAME = "AccessibleAcademicUnicode"


def _load_processed_content(lecture: Lecture) -> dict:
    try:
        # Cleaned on read as well as on write, so lectures processed before the
        # prose-\newline fix export correctly without a migration.
        return clean_study_content(json.loads(lecture.processed_content_json))
    except Exception:
        return {"topics": [], "summaries": [], "flashcards": []}


def _safe_title(value: str, fallback: str) -> str:
    safe = "".join(c for c in value if c not in r'\/:*?"<>|').strip() or fallback
    return safe.replace('\n', ' ').replace('\r', ' ')[:200]


def _candidate_font_paths() -> list[Path]:
    candidates: list[Path] = []
    env_font = os.getenv("PDF_FONT_PATH")
    if env_font:
        candidates.append(Path(env_font))
    candidates.extend([
        FONT_FILE,
        Path(r"C:\Windows\Fonts\segoeui.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path(r"C:\Windows\Fonts\calibri.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansHebrew-Regular.ttf"),
        Path("/Library/Fonts/Arial.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ])
    seen: set[str] = set()
    unique: list[Path] = []
    for p in candidates:
        key = str(p)
        if key not in seen:
            seen.add(key)
            if p.exists():
                unique.append(p)
    return unique


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


# ─── Segment splitter (display vs inline+text) ───────────────────────────────
_DISPLAY_RE = re.compile(r'(\$\$.+?\$\$|\\\[.+?\\\])', re.DOTALL)
_INLINE_RE  = re.compile(r'(\$.+?\$|\\\(.+?\\\))',      re.DOTALL)


def _split_segments(text: str) -> list[tuple[str, str]]:
    """Split text into ('display', latex), ('inline', latex), or ('text', str) segments."""
    segments: list[tuple[str, str]] = []
    for part in _DISPLAY_RE.split(text):
        if _DISPLAY_RE.fullmatch(part):
            inner = re.sub(r'^\$\$|\$\$$|^\\\[|\\\]$', '', part).strip()
            segments.append(('display', inner))
        else:
            for sub in _INLINE_RE.split(part):
                if _INLINE_RE.fullmatch(sub):
                    inner = re.sub(r'^\$|\$$|^\\\(|\\\)$', '', sub).strip()
                    segments.append(('inline', inner))
                elif sub:
                    segments.append(('text', sub))
    return segments


# ─── PDF writer ───────────────────────────────────────────────────────────────

def export_lecture_pdf(lecture_id: int, session: Session):
    lecture = session.get(Lecture, lecture_id)
    if not lecture or lecture.status != "completed":
        raise HTTPException(status_code=404, detail="Lecture not ready")

    data = _load_processed_content(lecture)

    pdf = FPDF()
    pdf.set_margins(20, 20, 20)
    pdf.add_page()
    usable_w = pdf.w - pdf.l_margin - pdf.r_margin

    font = _load_unicode_font(pdf)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def rtl_write(text: str, font_size: int) -> None:
        """Render a line of RTL text using bidi algorithm."""
        pdf.set_font(font, "", font_size)
        lines = pdf.multi_cell(usable_w, 8, txt=text, align="R", split_only=True)
        for line in lines:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(usable_w, 8, txt=get_display(line), align="R")

    def write_display_math(latex: str) -> None:
        """
        Render a display ($$...$$) math expression on its own centred line
        with a light-blue tinted box for visual separation.
        """
        converted = _process_math(latex)
        pdf.ln(3)
        # Draw a subtle filled box
        box_h = 10
        pdf.set_fill_color(235, 243, 255)   # very light blue
        pdf.set_draw_color(180, 210, 240)
        pdf.rect(pdf.l_margin, pdf.get_y(), usable_w, box_h, 'FD')
        # Write centred in the box
        pdf.set_font(font, "", 12)
        pdf.set_text_color(20, 60, 120)
        pdf.set_xy(pdf.l_margin, pdf.get_y())
        pdf.multi_cell(usable_w, box_h, txt=converted, align="C")
        pdf.set_text_color(0, 0, 0)
        pdf.set_draw_color(0, 0, 0)
        pdf.ln(3)

    def write_mixed(text: str, font_size: int) -> None:
        """
        Write a block of text that may contain display math ($$...$$).
        Display blocks are rendered with write_display_math().
        Everything else (including inline $...$) uses latex_to_text + RTL.
        """
        segments = _split_segments(text)
        # Accumulate non-display segments, flush before/after display blocks
        buf = ''
        for tag, content in segments:
            if tag == 'display':
                if buf.strip():
                    rtl_write(latex_to_text(buf), font_size)
                    buf = ''
                write_display_math(content)
            else:
                # inline math: convert to Unicode and fold into the text buffer
                if tag == 'inline':
                    buf += _process_math(content)
                else:
                    buf += content
        if buf.strip():
            rtl_write(latex_to_text(buf), font_size)

    def section_header(text: str) -> None:
        pdf.ln(5)
        pdf.set_font(font, "", 18)
        pdf.set_text_color(0, 51, 102)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(usable_w, 10, txt=get_display(text), align="R")
        pdf.set_draw_color(0, 51, 102)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(5)
        pdf.set_text_color(0, 0, 0)

    # ── Document body ─────────────────────────────────────────────────────────

    write_mixed(f"סיכום הרצאה: {lecture.title}", 22)
    pdf.ln(10)

    if data.get("topics"):
        section_header("נושאים מרכזיים")
        for topic in data["topics"]:
            write_mixed(f"• {topic}", 13)
        pdf.ln(5)

    if data.get("summaries"):
        section_header("סיכום מורחב")
        for item in data["summaries"]:
            write_mixed(f'{item.get("topic_name", "נושא")}:', 14)
            write_mixed(item.get("content", ""), 12)
            pdf.ln(2)
            pdf.set_draw_color(220, 220, 220)
            pdf.line(pdf.l_margin + 20, pdf.get_y(),
                     pdf.w - pdf.r_margin - 20, pdf.get_y())
            pdf.ln(4)

    if data.get("flashcards"):
        pdf.add_page()
        section_header("כרטיסיות זיכרון")
        for i, card in enumerate(data["flashcards"], 1):
            write_mixed(f'{i}. שאלה: {card.get("question", "")}', 13)
            write_mixed(f'תשובה: {card.get("answer", "")}', 12)
            pdf.ln(6)

    safe_title = _safe_title(lecture.title, f"lecture-{lecture_id}")
    export_path = EXPORTS_DIR / f"export_{lecture_id}.pdf"
    pdf.output(str(export_path))
    return FileResponse(export_path, filename=f"summery-{safe_title}.pdf")
