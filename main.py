from fastapi import FastAPI, BackgroundTasks, HTTPException, Depends
from sqlmodel import SQLModel, Field, create_engine, Session, select
from typing import List, Optional
import requests
import time
import os
import json
from openai import OpenAI
from fastapi.middleware.cors import CORSMiddleware

# --- 1. הגדרת בסיס הנתונים (SQLite) ---
sqlite_file_name = "database.db"
sqlite_url = f"sqlite:///./{sqlite_file_name}"
engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})


def get_session():
    with Session(engine) as session:
        yield session


# --- 2. מודל הנתונים (הטבלה ב-DB) ---
class Lecture(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    status: str
    transcript: str = ""
    words_json: str = "[]"  # שומרים את מערך המילים כטקסט JSON
    summary_and_cards: str = ""


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)


app = FastAPI()


# הרצת יצירת הטבלאות כשהשרת עולה
@app.on_event("startup")
def on_startup():
    create_db_and_tables()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 3. מפתחות API ---
ASSEMBLY_API_KEY = ""
OPENAI_API_KEY = ""

# --- 4. פונקציות הליבה (Logic) ---

def transcribe_audio(filename):
    """שלב א': תמלול האודיו ב-AssemblyAI"""
    headers = {'authorization': ASSEMBLY_API_KEY}

    def read_file(filename, chunk_size=5242880):
        with open(filename, 'rb') as _file:
            while True:
                data = _file.read(chunk_size)
                if not data: break
                yield data

    # העלאה
    upload_response = requests.post('https://api.assemblyai.com/v2/upload', headers=headers, data=read_file(filename))
    audio_url = upload_response.json()['upload_url']

    # בקשת תמלול
    json_data = {"audio_url": audio_url, "language_code": "he"}
    response = requests.post("https://api.assemblyai.com/v2/transcript", json=json_data, headers=headers)
    transcript_id = response.json()['id']

    # המתנה לתוצאה
    while True:
        polling_response = requests.get(f"https://api.assemblyai.com/v2/transcript/{transcript_id}", headers=headers)
        res_json = polling_response.json()
        if res_json['status'] == 'completed':
            return {"text": res_json['text'], "words": res_json['words']}
        elif res_json['status'] == 'error':
            raise Exception("Transcription failed")
        time.sleep(3)


# ************************************************************
# כאן היא נמצאת! הפונקציה שמייצרת את חומרי הלמידה
# ************************************************************
def generate_study_material(text):
    """שלב ב': יצירת סיכום וכרטיסיות ב-OpenAI"""
    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = f"""
    אתה עוזר אקדמי מומחה. נתח את תמלול ההרצאה הבא בעברית:
    "{text}"
    ספק את התוצרים הבאים בעברית:
    1. סיכום תמציתי בנקודות (Bullet points).
    2. 3 כרטיסיות זיכרון (שאלה ותשובה) על המושגים המרכזיים.
    שמור על מבנה ברור וקריא.
    """

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": "You are a helpful academic assistant."},
                  {"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content


# --- 5. פס הייצור (The Pipeline) ---

def run_full_pipeline(lecture_id: int, audio_filename: str):
    """מנהלת את התהליך ושומרת ב-Database בכל שלב"""
    with Session(engine) as session:
        lecture = session.get(Lecture, lecture_id)
        if not lecture: return

        try:
            # 1. הרצת תמלול
            result = transcribe_audio(audio_filename)
            lecture.transcript = result["text"]
            lecture.words_json = json.dumps(result["words"])  # שמירת חותמות זמן
            session.add(lecture)
            session.commit()

            # 2. הרצת יצירת חומרי למידה (כאן הקריאה לפונקציה שחיפשתם!)
            analysis = generate_study_material(result["text"])
            lecture.summary_and_cards = analysis

            # 3. סיום ועדכון סטטוס
            lecture.status = "completed"
            session.add(lecture)
            session.commit()
            print(f"✅ Lecture {lecture_id} finished processing.")

        except Exception as e:
            lecture.status = f"error: {str(e)}"
            session.add(lecture)
            session.commit()


# --- 6. Endpoints ---

@app.get("/lectures", response_model=List[Lecture])
def get_all_lectures(session: Session = Depends(get_session)):
    return session.exec(select(Lecture)).all()


@app.post("/process")
def process_lecture(title: str, filename: str, background_tasks: BackgroundTasks,
                    session: Session = Depends(get_session)):
    if not os.path.exists(filename):
        raise HTTPException(status_code=404, detail="File not found")

    new_lecture = Lecture(title=title, status="processing")
    session.add(new_lecture)
    session.commit()
    session.refresh(new_lecture)

    background_tasks.add_task(run_full_pipeline, new_lecture.id, filename)
    return {"message": "Started", "lecture_id": new_lecture.id}