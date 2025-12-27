from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI()

# --- מודלים של נתונים (המבנה של המידע) ---
class Flashcard(BaseModel):
    question: str
    answer: str

class LectureResult(BaseModel):
    id: int
    title: str
    transcript: str
    summary: str
    flashcards: List[Flashcard]

# --- נתוני דמה (Mock Data) - כדי שנוכל לעבוד בלי המכללה ---
db_mock = [
    {
        "id": 1,
        "title": "מבוא לסיבוכיות - הרצאה 1",
        "transcript": "שלום לכולם, היום נלמד על Big O...",
        "summary": "ההרצאה עסקה במושגי יסוד בסיבוכיות.",
        "flashcards": [{"question": "מה זה O(n)?", "answer": "זמן ריצה ליניארי"}]
    }
]

# --- Endpoints (נתיבי השרת) ---

@app.get("/")
def read_root():
    return {"message": "Accessible Academic Server is Running!"}

@app.get("/lectures", response_model=List[LectureResult])
def get_all_lectures():
    """מחזיר את רשימת כל ההרצאות המעובדות"""
    return db_mock

@app.get("/lectures/{lecture_id}", response_model=LectureResult)
def get_lecture(lecture_id: int):
    """שליפת הרצאה ספציפית לפי ID"""
    lecture = next((item for item in db_mock if item["id"] == lecture_id), None)
    if not lecture:
        raise HTTPException(status_code=404, detail="Lecture not found")
    return lecture

@app.post("/process")
def process_new_lecture(video_url: str):
    """
    כאן בעתיד יכנס הקוד שלכם מה-PoC:
    1. הורדת האודיו
    2. שליחה ל-AssemblyAI
    3. ניתוח ב-OpenAI
    """
    return {"message": f"Processing started for {video_url}"}