import os
import time
from typing import Optional

import requests
from openai import BadRequestError, OpenAI


ASSEMBLY_API_KEY = os.getenv("ASSEMBLY_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODELS = [
    m.strip()
    for m in os.getenv("OPENAI_MODELS", "gpt-4.1-mini,gpt-4o-mini,gpt-4.1").split(",")
    if m.strip()
]


def _assembly_headers():
    return {"authorization": ASSEMBLY_API_KEY}


def transcribe_audio(filename: str, lecture_id: Optional[int] = None, progress_cb=None):
    """Upload audio to AssemblyAI, submit transcript job, poll until done."""
    headers = _assembly_headers()

    def read_file(fn):
        with open(fn, "rb") as _f:
            while chunk := _f.read(5242880):
                yield chunk

    if lecture_id is not None and progress_cb:
        progress_cb(lecture_id, "uploading", 5)

    up_res = requests.post("https://api.assemblyai.com/v2/upload", headers=headers, data=read_file(filename))
    audio_url = up_res.json()["upload_url"]

    tx_res = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        json={"audio_url": audio_url, "language_code": "he"},
        headers=headers,
    )
    tx_id = tx_res.json()["id"]

    if lecture_id is not None and progress_cb:
        progress_cb(lecture_id, "transcribing", 15, assemblyai_transcript_id=tx_id)

    while True:
        res = requests.get(f"https://api.assemblyai.com/v2/transcript/{tx_id}", headers=headers).json()
        status = res.get("status", "")
        if lecture_id is not None and progress_cb:
            if status == "queued":
                progress_cb(lecture_id, "transcribing", 20)
            elif status == "processing":
                progress_cb(lecture_id, "transcribing", 50)
        if status == "completed":
            return {"text": res["text"], "words": res.get("words", [])}
        if status == "error":
            raise Exception(res.get("error", "Transcription failed"))
        time.sleep(3)


def generate_study_material(text: str):
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

    last_err = None
    for model_name in OPENAI_MODELS:
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert academic professor. You write detailed, long, and structured summaries in Hebrew. You ensure each summary is comprehensive.",
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            return response.choices[0].message.content
        except BadRequestError as e:
            err_body = getattr(e, "body", {}) or {}
            err_code = ((err_body.get("error") or {}).get("code")) if isinstance(err_body, dict) else None
            if err_code in {"model_not_found", "unsupported_model"}:
                last_err = e
                continue
            raise
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(
        "Failed to generate study material with configured OpenAI models: " + ", ".join(OPENAI_MODELS)
    ) from last_err
