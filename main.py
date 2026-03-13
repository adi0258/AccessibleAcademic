from dotenv import load_dotenv
load_dotenv()

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
from bidi.algorithm import get_display

# --- 1. הגדרת בסיס הנתונים ---
sqlite_url = "sqlite:///./database.db"
engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})

def get_session():
    with Session(engine) as session:
        yield session

# --- 2. מודל הנתונים (מעודכן עם השדה החדש) ---
class Lecture(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    filename: str  
    status: str
    transcript: str = ""
    words_json: str = "[]"
    processed_content_json: str = "{}" # כאן יישמרו 3 המשימות

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

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

@app.on_event("startup")
def on_startup():
    create_db_and_tables()

ASSEMBLY_API_KEY = os.getenv("ASSEMBLY_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# --- 4. לוגיקת AI ---

def transcribe_audio(filename):
    headers = {'authorization': ASSEMBLY_API_KEY}
    def read_file(fn):
        with open(fn, 'rb') as _f:
            while chunk := _f.read(5242880): yield chunk

    # העלאה
    up_res = requests.post('https://api.assemblyai.com/v2/upload', headers=headers, data=read_file(filename))
    audio_url = up_res.json()['upload_url']

    # בקשת תמלול
    tx_res = requests.post("https://api.assemblyai.com/v2/transcript",
                           json={"audio_url": audio_url, "language_code": "he"}, headers=headers)
    tx_id = tx_res.json()['id']

    # Polling
    while True:
        res = requests.get(f"https://api.assemblyai.com/v2/transcript/{tx_id}", headers=headers).json()
        if res['status'] == 'completed':
            return {"text": res['text'], "words": res['words']}
        if res['status'] == 'error':
            raise Exception("Transcription failed")
        time.sleep(3)

def generate_study_material(text):
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    # ה-Prompt המדויק ל-3 המשימות שלך בפורמט JSON
    prompt = f"""
    נתח את תמלול ההרצאה הבא בעברית והחזר תשובה בפורמט JSON בלבד.
    ה-JSON חייב להכיל בדיוק את המפתחות הבאים:
    1. "topics": רשימה (list) של הנושאים המרכזיים בנקודות.
    2. "summaries": רשימה (list) של סיכומים קצרים לכל נושא.
    3. "flashcards": רשימה של אובייקטים עם "question" ו-"answer".

    התמלול:
    {text}
    """

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are an academic assistant that strictly outputs JSON in Hebrew."},
            {"role": "user", "content": prompt}
        ],
        response_format={ "type": "json_object" }
    )
    return response.choices[0].message.content

# --- 5. ה-Pipeline ---
def run_full_pipeline(lecture_id: int, audio_filename: str):
    with Session(engine) as session:
        lecture = session.get(Lecture, lecture_id)
        try:
            # שלב 1: תמלול
            result = transcribe_audio(audio_filename)
            lecture.transcript = result["text"]
            lecture.words_json = json.dumps(result["words"])
            session.add(lecture)
            session.commit()

            # שלב 2: 3 המשימות (נושאים, סיכום, כרטיסיות)
            lecture.processed_content_json = generate_study_material(result["text"])
            lecture.status = "completed"
        except Exception as e:
            print(f"Pipeline Error: {str(e)}")
            lecture.status = f"error: {str(e)}"
        finally:
            session.add(lecture)
            session.commit()

# --- 6. Endpoints ---

@app.post("/process")
def process_lecture(title: str, filename: str, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    file_path = os.path.join("recordings", filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    new_lecture = Lecture(title=title, filename=filename, status="processing")
    session.add(new_lecture)
    session.commit()
    session.refresh(new_lecture)

    background_tasks.add_task(run_full_pipeline, new_lecture.id, file_path)
    return {"message": "Started", "lecture_id": new_lecture.id}

@app.get("/lectures", response_model=List[Lecture])
def get_all_lectures(session: Session = Depends(get_session)):
    return session.exec(select(Lecture)).all()

@app.get("/lectures/{lecture_id}/export")
def export_lecture_pdf(lecture_id: int, session: Session = Depends(get_session)):
    lecture = session.get(Lecture, lecture_id)
    if not lecture or lecture.status != "completed":
        raise HTTPException(status_code=404, detail="Lecture not ready")

    data = json.loads(lecture.processed_content_json)
    pdf = FPDF()
    pdf.add_page()
    
    font_path = "Heebo-VariableFont_wght.ttf" # וודא שזה השם המדויק של הקובץ אצלך
    if os.path.exists(font_path):
        pdf.add_font('Heebo', '', font_path, uni=True)
        pdf.set_font('Heebo', '', 14)
    else:
        pdf.set_font("Arial", size=12)

    def add_rtl_section(title, content_list):
        pdf.set_font('Heebo', '', 16)
        pdf.multi_cell(0, 10, txt=get_display(title), align='R')
        pdf.set_font('Heebo', '', 12)
        for item in content_list:
            if isinstance(item, dict): # לכרטיסיות
                text = f"שאלה: {item.get('question')} | תשובה: {item.get('answer')}"
            else:
                text = f"• {item}"
            pdf.multi_cell(0, 10, txt=get_display(text), align='R')
        pdf.ln(5)

    add_rtl_section(f"סיכום הרצאה: {lecture.title}", [])
    add_rtl_section("נושאים מרכזיים:", data.get("topics", []))
    add_rtl_section("סיכום מורחב:", data.get("summaries", []))
    add_rtl_section("כרטיסיות זיכרון:", data.get("flashcards", []))

    export_path = f"export_{lecture_id}.pdf"
    pdf.output(export_path)
    return FileResponse(export_path, filename=f"summary_{lecture_id}.pdf")