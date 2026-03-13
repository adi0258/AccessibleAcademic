from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, BackgroundTasks, HTTPException, Depends, File, UploadFile
from sqlmodel import SQLModel, Field, create_engine, Session, select
from typing import List, Optional
import requests
import time
import os
import json
from openai import OpenAI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fpdf import FPDF
from bidi.algorithm import get_display

# --- 1. הגדרת בסיס הנתונים ---
sqlite_url = "sqlite:///./database.db"
engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})

def get_session():
    with Session(engine) as session:
        yield session

# --- 2. מודל הנתונים ---
class Lecture(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    filename: str  
    status: str
    transcript: str = ""
    words_json: str = "[]"
    processed_content_json: str = "{}"
    # Progress tracking (AssemblyAI stage + derived percentage)
    assemblyai_transcript_id: Optional[str] = None
    processing_stage: Optional[str] = None  # uploading, transcribing, generating_study_material, completed
    progress_percent: Optional[int] = None  # 0-100

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    # Add progress columns if they don't exist (for existing DBs)
    from sqlalchemy import text
    with engine.connect() as conn:
        r = conn.execute(text("PRAGMA table_info(lecture)"))
        cols = {row[1] for row in r}
        for col, spec in [
            ("assemblyai_transcript_id", "TEXT"),
            ("processing_stage", "TEXT"),
            ("progress_percent", "INTEGER"),
        ]:
            if col not in cols:
                conn.execute(text(f"ALTER TABLE lecture ADD COLUMN {col} {spec}"))
        conn.commit()

app = FastAPI(title="Accessible Academic Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if not os.path.exists("recordings"):
    os.makedirs("recordings")

app.mount("/static", StaticFiles(directory="recordings"), name="static")

# --- UI: serve index page and file upload ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@app.get("/", response_class=FileResponse)
def serve_ui():
    """Serve the lecture transcription UI."""
    return FileResponse(os.path.join(BASE_DIR, "index.html"))


@app.get("/watch/{lecture_id}", response_class=FileResponse)
def watch_lecture(lecture_id: int):
    """Serve the watch-with-captions page for a lecture."""
    return FileResponse(os.path.join(BASE_DIR, "watch.html"))

@app.post("/upload")
def upload_audio(file: UploadFile = File(...)):
    """Accept an audio/video file and save it to recordings/. Returns the filename for use with /process."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")
    safe_name = os.path.basename(file.filename)
    file_path = os.path.join("recordings", safe_name)
    try:
        contents = file.file.read()
        with open(file_path, "wb") as f:
            f.write(contents)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"filename": safe_name}

@app.on_event("startup")
def on_startup():
    create_db_and_tables()

ASSEMBLY_API_KEY = os.getenv("ASSEMBLY_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# --- 4. לוגיקת AI ---

def _assembly_headers():
    return {'authorization': ASSEMBLY_API_KEY}

def _update_lecture_progress(lecture_id: int, processing_stage: str, progress_percent: int, assemblyai_transcript_id: Optional[str] = None):
    """Persist progress so the API can return it to the UI."""
    with Session(engine) as session:
        lecture = session.get(Lecture, lecture_id)
        if lecture:
            lecture.processing_stage = processing_stage
            lecture.progress_percent = progress_percent
            if assemblyai_transcript_id is not None:
                lecture.assemblyai_transcript_id = assemblyai_transcript_id
            session.add(lecture)
            session.commit()

def transcribe_audio(filename, lecture_id: Optional[int] = None):
    """Upload audio to AssemblyAI, submit transcript job, poll until done. Optionally report progress to DB."""
    headers = _assembly_headers()
    def read_file(fn):
        with open(fn, 'rb') as _f:
            while chunk := _f.read(5242880): yield chunk

    if lecture_id is not None:
        _update_lecture_progress(lecture_id, "uploading", 5)

    up_res = requests.post('https://api.assemblyai.com/v2/upload', headers=headers, data=read_file(filename))
    audio_url = up_res.json()['upload_url']

    tx_res = requests.post("https://api.assemblyai.com/v2/transcript",
                           json={"audio_url": audio_url, "language_code": "he"}, headers=headers)
    tx_id = tx_res.json()['id']

    if lecture_id is not None:
        _update_lecture_progress(lecture_id, "transcribing", 15, assemblyai_transcript_id=tx_id)

    while True:
        res = requests.get(f"https://api.assemblyai.com/v2/transcript/{tx_id}", headers=headers).json()
        status = res.get('status', '')
        if lecture_id is not None:
            if status == 'queued':
                _update_lecture_progress(lecture_id, "transcribing", 20)
            elif status == 'processing':
                _update_lecture_progress(lecture_id, "transcribing", 50)
        if status == 'completed':
            return {"text": res['text'], "words": res.get('words', [])}
        if status == 'error':
            raise Exception(res.get('error', 'Transcription failed'))
        time.sleep(3)

def generate_study_material(text):
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    prompt = f"""
    נתח את תמלול ההרצאה האקדמית הבא בעברית והחזר JSON בלבד.
    הסיכומים חייבים להיות מקצועיים, אקדמיים ומפורטים מאוד.
    
    על ה-JSON להכיל:
    1. "topics": רשימה של כותרות הנושאים המרכזיים בקצרה.
    2. "summaries": רשימה של אובייקטים. כל אובייקט מכיל:
       - "topic_name": שם הנושא.
       - "content": סיכום מעמיק ומפורט (לפחות 4-6 משפטים) על הנושא הספציפי מתוך ההרצאה.
    3. "flashcards": רשימה של 5 אובייקטים עם "question" ו-"answer".

    התמלול:
    {text}
    """

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are an expert academic professor. You write detailed, long, and structured summaries in Hebrew. You ensure each summary is comprehensive."},
            {"role": "user", "content": prompt}
        ],
        response_format={ "type": "json_object" }
    )
    return response.choices[0].message.content

# --- 5. ה-Pipeline ---
def run_full_pipeline(lecture_id: int, audio_filename: str):
    with Session(engine) as session:
        lecture = session.get(Lecture, lecture_id)
        if not lecture:
            return
        try:
            result = transcribe_audio(audio_filename, lecture_id=lecture_id)
            with Session(engine) as session2:
                lec = session2.get(Lecture, lecture_id)
                if lec:
                    lec.transcript = result["text"]
                    lec.words_json = json.dumps(result.get("words", []))
                    session2.add(lec)
                    session2.commit()

            _update_lecture_progress(lecture_id, "generating_study_material", 85)
            processed = generate_study_material(result["text"])
            with Session(engine) as session3:
                lec = session3.get(Lecture, lecture_id)
                if lec:
                    lec.processed_content_json = processed
                    lec.status = "completed"
                    lec.processing_stage = "completed"
                    lec.progress_percent = 100
                    session3.add(lec)
                    session3.commit()
        except Exception as e:
            print(f"Pipeline Error: {str(e)}")
            with Session(engine) as session_err:
                lec = session_err.get(Lecture, lecture_id)
                if lec:
                    lec.status = f"error: {str(e)}"
                    lec.processing_stage = "error"
                    session_err.add(lec)
                    session_err.commit()

# --- 6. Endpoints ---

@app.post("/process")
def process_lecture(title: str, filename: str, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    file_path = os.path.join("recordings", filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    new_lecture = Lecture(
        title=title,
        filename=filename,
        status="processing",
        processing_stage="pending",
        progress_percent=0,
    )
    session.add(new_lecture)
    session.commit()
    session.refresh(new_lecture)

    background_tasks.add_task(run_full_pipeline, new_lecture.id, file_path)
    return {"message": "Started", "lecture_id": new_lecture.id}

@app.get("/lectures", response_model=List[Lecture])
def get_all_lectures(session: Session = Depends(get_session)):
    return session.exec(select(Lecture)).all()

@app.get("/lectures/{lecture_id}", response_model=Lecture)
def get_lecture(lecture_id: int, session: Session = Depends(get_session)):
    lecture = session.get(Lecture, lecture_id)
    if not lecture:
        raise HTTPException(status_code=404, detail="Lecture not found")
    return lecture

# --- 7. ייצוא ל-PDF (תיקון חיתוך המילים) ---
@app.get("/lectures/{lecture_id}/export")
def export_lecture_pdf(lecture_id: int, session: Session = Depends(get_session)):
    lecture = session.get(Lecture, lecture_id)
    if not lecture or lecture.status != "completed":
        raise HTTPException(status_code=404, detail="Lecture not ready")

    try:
        data = json.loads(lecture.processed_content_json)
    except:
        data = {"topics": [], "summaries": [], "flashcards": []}

    pdf = FPDF()
    pdf.set_margins(20, 20, 20)
    pdf.add_page()
    usable_width = pdf.w - pdf.l_margin - pdf.r_margin

    font_name = "Arial"
    font_path = "Heebo-VariableFont_wght.ttf" 
    if os.path.exists(font_path):
        try:
            pdf.add_font('Heebo', '', font_path, uni=True)
            font_name = 'Heebo'
        except: pass

    # --- פונקציית עזר לפתרון בעיית ה-RTL והחיתוך ---
    def write_rtl_multiline(text, font_size, is_bold=False):
        pdf.set_font(font_name, '', font_size)
        # חותכים את הטקסט לשורות לפי רוחב העמוד לפני ההפיכה (get_display)
        lines = pdf.multi_cell(usable_width, 8, txt=text, align='R', split_only=True)
        for line in lines:
            pdf.set_x(pdf.l_margin)
            # הופכים כל שורה בנפרד ומדפיסים
            pdf.multi_cell(usable_width, 8, txt=get_display(line), align='R')

    def add_section_header(text):
        pdf.ln(5)
        pdf.set_font(font_name, '', 18)
        pdf.set_text_color(0, 51, 102)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(usable_width, 10, txt=get_display(text), align='R')
        pdf.set_draw_color(0, 51, 102)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(5)
        pdf.set_text_color(0, 0, 0)

    # 1. כותרת
    write_rtl_multiline(f"סיכום הרצאה: {lecture.title}", 22)
    pdf.ln(10)

    # 2. נושאים
    if data.get("topics"):
        add_section_header("נושאים מרכזיים")
        for topic in data["topics"]:
            write_rtl_multiline(f"• {topic}", 13)
        pdf.ln(5)

    # 3. סיכום מורחב (עם הפרדות)
    if data.get("summaries"):
        add_section_header("סיכום מורחב")
        for item in data["summaries"]:
            topic_title = item.get('topic_name', 'נושא')
            content = item.get('content', '')
            
            # הדפסת שם הנושא מודגש
            write_rtl_multiline(f"{topic_title}:", 14)
            # הדפסת התוכן המפורט
            write_rtl_multiline(content, 12)
            
            # קו הפרדה
            pdf.ln(2)
            pdf.set_draw_color(220, 220, 220)
            pdf.line(pdf.l_margin + 20, pdf.get_y(), pdf.w - pdf.r_margin - 20, pdf.get_y())
            pdf.ln(4)

    # 4. כרטיסיות
    if data.get("flashcards"):
        pdf.add_page() # דף חדש לכרטיסיות
        add_section_header("כרטיסיות זיכרון")
        for i, card in enumerate(data["flashcards"], 1):
            write_rtl_multiline(f"{i}. שאלה: {card.get('question')}", 13)
            write_rtl_multiline(f"תשובה: {card.get('answer')}", 12)
            pdf.ln(6)

    export_path = f"export_{lecture_id}.pdf"
    pdf.output(export_path)
    return FileResponse(export_path, filename=f"summary_{lecture_id}.pdf")