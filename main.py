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

# --- 2. מודל הנתונים ---
class Lecture(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    filename: str  
    status: str
    transcript: str = ""
    words_json: str = "[]"
    processed_content_json: str = "{}"

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

    up_res = requests.post('https://api.assemblyai.com/v2/upload', headers=headers, data=read_file(filename))
    audio_url = up_res.json()['upload_url']

    tx_res = requests.post("https://api.assemblyai.com/v2/transcript",
                           json={"audio_url": audio_url, "language_code": "he"}, headers=headers)
    tx_id = tx_res.json()['id']

    while True:
        res = requests.get(f"https://api.assemblyai.com/v2/transcript/{tx_id}", headers=headers).json()
        if res['status'] == 'completed':
            return {"text": res['text'], "words": res['words']}
        if res['status'] == 'error':
            raise Exception("Transcription failed")
        time.sleep(3)

def generate_study_material(text):
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    # הפרומפט המשודרג: דורש JSON עם הפרדה בין שם הנושא לתוכן שלו
    prompt = f"""
    נתח את תמלול ההרצאה האקדמית הבא בעברית והחזר JSON בלבד.
    על ה-JSON להכיל:
    1. "topics": רשימה של כותרות הנושאים המרכזיים בקצרה.
    2. "summaries": רשימה של אובייקטים. כל אובייקט מכיל "topic_name" ו-"content" (סיכום מפורט ומעמיק של אותו נושא על בסיס ההרצאה).
    3. "flashcards": רשימה של אובייקטים עם "question" ו-"answer".

    התמלול:
    {text}
    """

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are an expert academic assistant. Your summaries are detailed, structured, and strictly based on the provided transcript."},
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
            result = transcribe_audio(audio_filename)
            lecture.transcript = result["text"]
            lecture.words_json = json.dumps(result["words"])
            session.add(lecture)
            session.commit()

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

# --- 7. ייצוא ל-PDF (החלק המעוצב מחדש) ---
@app.get("/lectures/{lecture_id}/export")
def export_lecture_pdf(lecture_id: int, session: Session = Depends(get_session)):
    lecture = session.get(Lecture, lecture_id)
    if not lecture or lecture.status != "completed":
        raise HTTPException(status_code=404, detail="Lecture not ready")

    try:
        data = json.loads(lecture.processed_content_json)
    except json.JSONDecodeError:
        data = {"topics": [], "summaries": [], "flashcards": []}

    pdf = FPDF()
    pdf.set_margins(20, 20, 20)
    pdf.add_page()
    usable_width = pdf.w - pdf.l_margin - pdf.r_margin

    font_name = "Arial"
    font_path = "Heebo-VariableFont_wght.ttf" # וודאו שהקובץ קיים בתיקייה
    if os.path.exists(font_path):
        try:
            pdf.add_font('Heebo', '', font_path, uni=True)
            font_name = 'Heebo'
        except: pass

    # פונקציית עזר לכותרות סקציה
    def add_section_header(text):
        pdf.set_font(font_name, '', 18)
        pdf.set_text_color(0, 51, 102) # כחול כהה אקדמי
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(usable_width, 12, txt=get_display(text), align='R')
        pdf.set_draw_color(0, 51, 102)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(5)
        pdf.set_text_color(0, 0, 0) # חזרה לשחור

    # כותרת ההרצאה
    pdf.set_font(font_name, '', 22)
    pdf.multi_cell(usable_width, 15, txt=get_display(f"סיכום הרצאה: {lecture.title}"), align='R')
    pdf.ln(10)

    # 1. נושאים מרכזיים (נקודות)
    if data.get("topics"):
        add_section_header("נושאים מרכזיים")
        pdf.set_font(font_name, '', 13)
        for topic in data["topics"]:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(usable_width, 9, txt=get_display(f"• {topic}"), align='R')
        pdf.ln(10)

    # 2. סיכום מורחב (עם קווי הפרדה ושם נושא מודגש)
    if data.get("summaries"):
        add_section_header("סיכום מורחב")
        for item in data["summaries"]:
            # שם הנושא
            pdf.set_font(font_name, '', 14)
            topic_name = item.get('topic_name', 'נושא')
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(usable_width, 10, txt=get_display(f"{topic_name}:"), align='R')
            
            # תוכן הסיכום
            pdf.set_font(font_name, '', 12)
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(usable_width, 8, txt=get_display(item.get('content', '')), align='R')
            
            # קו הפרדה עדין בין נושאים
            pdf.set_draw_color(200, 200, 200)
            pdf.ln(3)
            pdf.line(pdf.l_margin + 50, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(5)
        pdf.ln(5)

    # 3. כרטיסיות זיכרון (מספור וירידת שורה)
    if data.get("flashcards"):
        add_section_header("כרטיסיות זיכרון")
        pdf.set_font(font_name, '', 12)
        for i, card in enumerate(data["flashcards"], 1):
            pdf.set_x(pdf.l_margin)
            # שאלה
            question_text = f"{i}. שאלה: {card.get('question')}"
            pdf.multi_cell(usable_width, 8, txt=get_display(question_text), align='R')
            # תשובה (בירידת שורה)
            pdf.set_x(pdf.l_margin)
            answer_text = f"תשובה: {card.get('answer')}"
            pdf.multi_cell(usable_width, 8, txt=get_display(answer_text), align='R')
            pdf.ln(5)

    export_path = f"export_{lecture_id}.pdf"
    pdf.output(export_path)
    return FileResponse(export_path, filename=f"summary_{lecture_id}.pdf")