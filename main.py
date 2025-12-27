from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import List
import requests
import time
import os
from openai import OpenAI

# יצירת מופע של האפליקציה. זהו "מרכז הבקרה" של השרת
app = FastAPI()

# --- הגדרות ומפתחות ---
ASSEMBLY_API_KEY = ""
OPENAI_API_KEY = ""

# בסיס נתונים זמני (In-Memory). ב-C זה היה מערך של structs.
# שימו לב: אם מכבים את השרת, המידע נמחק.
db_mock = []


# --- מודלים של נתונים (Pydantic) ---
# זהו המקביל ל-struct ב-C.
# היתרון ב-FastAPI: הוא משתמש בזה כדי לבצע וולידציה אוטומטית לקלט/פלט.
class LectureResult(BaseModel):
    id: int
    title: str
    status: str
    transcript: str = ""
    summary_and_cards: str = ""


# --- פונקציות עזר (Logic) ---

def transcribe_audio(filename):
    """שולחת אודיו לתמלול וממתינה לתוצאה (Polling)"""
    headers = {'authorization': ASSEMBLY_API_KEY}

    # העלאת הקובץ לשרת של AssemblyAI (כפי שהסברנו עם ה-Generator)
    def read_file(filename, chunk_size=5242880):
        with open(filename, 'rb') as _file:
            while True:
                data = _file.read(chunk_size)
                if not data: break
                yield data

    upload_response = requests.post('https://api.assemblyai.com/v2/upload', headers=headers, data=read_file(filename))
    audio_url = upload_response.json()['upload_url']

    # בקשת התמלול
    json_data = {"audio_url": audio_url, "language_code": "he"}
    response = requests.post("https://api.assemblyai.com/v2/transcript", json=json_data, headers=headers)
    transcript_id = response.json()['id']

    # לולאת המתנה (כמו ב-POC המקורי)
    while True:
        polling_response = requests.get(f"https://api.assemblyai.com/v2/transcript/{transcript_id}", headers=headers)
        res_json = polling_response.json()
        status = res_json['status']

        if status == 'completed':
            return res_json['text']
        elif status == 'error':
            raise Exception(f"Transcription failed: {res_json.get('error')}")

        time.sleep(3)  # מונע הצפה של ה-API בבקשות


def generate_study_material(text):
    print("\n4. Sending transcript to GPT-4o for analysis...")
    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = f"""
    You are an expert tutor for university students.
    Analyze the following lecture transcript (in Hebrew):

    "{text}"

    Please provide output in Hebrew:
    1. A concise summary (bullet points).
    2. 3 Flashcards (Question and Answer) based on key concepts.

    Format the output clearly.
    """

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are a helpful academic assistant."},
            {"role": "user", "content": prompt}
        ]
    )

    return response.choices[0].message.content


# --- תהליך הרקע (The Pipeline) ---

def run_full_pipeline(lecture_id: int, audio_filename: str):
    """
    הפונקציה הזו מריצה את כל תהליך ה-AI.
    היא עובדת ברקע כדי שהמשתמש לא יצטרך לחכות דקות ארוכות לתגובת ה-HTTP.
    """
    # חיפוש ההרצאה ב"בסיס הנתונים" שלנו
    lecture = next((item for item in db_mock if item["id"] == lecture_id), None)
    if not lecture:
        return

    try:
        # שלב 1: תמלול (לוקח זמן)
        text = transcribe_audio(audio_filename)
        lecture["transcript"] = text

        # שלב 2: ניתוח GPT (לוקח זמן)
        analysis = generate_study_material(text)
        lecture["summary_and_cards"] = analysis

        # שלב 3: עדכון סטטוס לסיום
        lecture["status"] = "completed"

    except Exception as e:
        # אם משהו נכשל, נעדכן את הסטטוס כדי שהמשתמש ידע מה קרה
        lecture["status"] = f"error: {str(e)}"


# --- נתיבי השרת (Endpoints) ---

@app.get("/lectures", response_model=List[LectureResult])
def get_all_lectures():
    """
    נתיב GET: מחזיר את כל ההרצאות הקיימות.
    המשתמש יכול לקרוא לזה שוב ושוב כדי לבדוק אם הסטטוס השתנה מ-processing ל-completed.
    """
    return db_mock


@app.post("/process")
def process_lecture(title: str, filename: str, background_tasks: BackgroundTasks):
    """
    נתיב POST: מקבל פקודה להתחיל עיבוד של הרצאה חדשה.
    1. בודק אם הקובץ קיים.
    2. יוצר רשומה ב-db עם סטטוס 'processing'.
    3. שולח את העבודה הכבדה לרקע ומחזיר תשובה מיידית למשתמש.
    """
    if not os.path.exists(filename):
        raise HTTPException(status_code=404, detail="Audio file not found on server")

    # יצירת ID חדש (פשוט רץ רציף)
    lecture_id = len(db_mock) + 1
    new_lecture = {
        "id": lecture_id,
        "title": title,
        "status": "processing",
        "transcript": "",
        "summary_and_cards": ""
    }
    db_mock.append(new_lecture)

    # פקודה ל-FastAPI להריץ את ה-pipeline ברקע (Async-like behavior)
    background_tasks.add_task(run_full_pipeline, lecture_id, filename)

    # המשתמש מקבל תשובה מיידית עם ה-ID של הבקשה שלו
    return {"message": "Processing started", "lecture_id": lecture_id}