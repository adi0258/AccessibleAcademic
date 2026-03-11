from fastapi import FastAPI, BackgroundTasks, HTTPException, Depends
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
from bidi.algorithm import get_display  # לטיפול בטקסט מימין לשמאל

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
    # שדה חדש שיכיל את כל המידע המעובד (נושאים, סיכומים וכרטיסיות)
    processed_content_json: str = "{}"
    
def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

app = FastAPI(title="Accessible Academic Backend")

# --- 3. הגדרות שרת וקבצים סטטיים ---
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

@app.on_event("startup")
def on_startup():
    create_db_and_tables()

# מפתחות API (השתמשו במפתחות שלכם)
ASSEMBLY_API_KEY = os.getenv("ASSEMBLY_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# --- 4. לוגיקת AI ---
def transcribe_audio(filename):
    headers = {'authorization': ASSEMBLY_API_KEY}
    def read_file(fn):
        with open(fn, 'rb') as _f:
            while chunk := _f.read(5242880): yield chunk

    up_res = requests.post('https://api.assemblyai.com/v2/upload', headers=headers, data=read_file(filename))
    audio_url = up_res.json()['upload_url']

    tx_res = requests.post("https://api.assemblyai.com/v2/transcript", 
                           json={"audio_url": audio_url, "language_code": "he"}, headers=headers)
    tx_id = tx_res.json()['id']

    while True:
        res = requests.get(f"https://api.assemblyai.com/v2/transcript/{tx_id}", headers=headers).json()
        if res['status'] == 'completed': return {"text": res['text'], "words": res['words']}
        if res['status'] == 'error': raise Exception("Transcription failed")
        time.sleep(3)

def generate_study_material(text):
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    prompt = f"""
    נתח את תמלול ההרצאה הבא בעברית והחזר תשובה בפורמט JSON בלבד.
    על ה-JSON להכיל:
    1. "topics": רשימה של הנושאים המרכזיים שעלו (בנקודות).
    2. "summaries": סיכום מפורט לכל נושא מרכזי שצוין.
    3. "flashcards": רשימה של 3 כרטיסיות זיכרון (שאלה ותשובה).

    התמלול:
    {text}
    """

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are an academic assistant that outputs only valid JSON."},
            {"role": "user", "content": prompt}
        ],
        response_format={ "type": "json_object" } # מבטיח שנקבל JSON תקין
    )
    return response.choices[0].message.content

# --- 5. ה-Pipeline ---
def run_full_pipeline(lecture_id: int, audio_filename: str):
    with Session(engine) as session:
        lecture = session.get(Lecture, lecture_id)
        try:
            result = transcribe_audio(audio_filename)
            lecture.transcript = result["text"]
            lecture.words_json = json.dumps(result["words"])
            session.add(lecture)
            session.commit()

            lecture.processed_content_json = generate_study_material(result["text"])
            lecture.status = "completed"
        except Exception as e:
            lecture.status = f"error: {str(e)}"
        finally:
            # המחיקה הופסקה כדי לשמור את הקבצים לצפייה ב-Frontend
            # if os.path.exists(audio_filename):
            #     os.remove(audio_filename)
            session.add(lecture)
            session.commit()

# --- 6. Endpoints ---

@app.post("/process")
def process_lecture(title: str, filename: str, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    file_path = os.path.join("recordings", filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found in recordings folder")

    existing = session.exec(select(Lecture).where(Lecture.filename == filename, Lecture.status == "completed")).first()
    if existing:
        return {"message": "Lecture already processed", "lecture_id": existing.id, "data": existing}

    new_lecture = Lecture(title=title, filename=filename, status="processing")
    session.add(new_lecture)
    session.commit()
    session.refresh(new_lecture)

    background_tasks.add_task(run_full_pipeline, new_lecture.id, file_path)
    return {"message": "Started new processing", "lecture_id": new_lecture.id}

@app.get("/lectures", response_model=List[Lecture])
def get_all_lectures(session: Session = Depends(get_session)):
    return session.exec(select(Lecture)).all()

# --- 7. Endpoint: ייצוא ל-PDF תומך עברית ---
@app.get("/lectures/{lecture_id}/export")
def export_lecture_pdf(lecture_id: int, session: Session = Depends(get_session)):
    lecture = session.get(Lecture, lecture_id)
    if not lecture:
        raise HTTPException(status_code=404, detail="Lecture not found")

    data = json.loads(lecture.processed_content_json)
    pdf = FPDF()
    pdf.add_page()
    
    font_path = "Heebo-VariableFont_wght.ttf"
    if os.path.exists(font_path):
        pdf.add_font('Heebo', '', font_path, uni=True)
        pdf.set_font('Heebo', '', 14)
    
    # פונקציית עזר להוספת טקסט RTL
    def add_rtl_text(text, size=12, is_title=False):
        if is_title: pdf.set_font('Heebo', '', size + 4)
        else: pdf.set_font('Heebo', '', size)
        pdf.multi_cell(0, 10, txt=get_display(text), align='R')
        pdf.ln(5)

    add_rtl_text(f"סיכום הרצאה: {lecture.title}", size=18, is_title=True)

    # 1. נושאים מרכזיים
    add_rtl_text("נושאים מרכזיים:", is_title=True)
    for topic in data.get("topics", []):
        add_rtl_text(f"• {topic}")

    # 2. סיכומים
    add_rtl_text("סיכום מורחב:", is_title=True)
    for summary in data.get("summaries", []):
        add_rtl_text(summary)

    # 3. כרטיסיות זיכרון
    add_rtl_text("כרטיסיות זיכרון:", is_title=True)
    for card in data.get("flashcards", []):
        q = card.get('question', card.get('שאלה'))
        a = card.get('answer', card.get('תשובה'))
        add_rtl_text(f"שאלה: {q}")
        add_rtl_text(f"תשובה: {a}")
        pdf.ln(5)

    export_path = f"export_{lecture_id}.pdf"
    pdf.output(export_path)
    return FileResponse(export_path, filename=f"summary_{lecture_id}.pdf")
