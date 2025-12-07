import requests
import time
from openai import OpenAI
import os

# --- הגדרות (נא למלא את המפתחות שלכם כאן) ---

# שם קובץ האודיו לבדיקה (ודאו שהוא קיים באותה תיקייה)
AUDIO_FILENAME = "test.mp4"


# --- פונקציה 1: העלאה ותמלול ב-AssemblyAI ---
def transcribe_audio(filename):
    print(f"1. Uploading {filename} to AssemblyAI...")
    headers = {'authorization': ASSEMBLY_API_KEY}

    # שלב א: העלאת הקובץ לשרת של Assembly
    def read_file(filename, chunk_size=5242880):
        with open(filename, 'rb') as _file:
            while True:
                data = _file.read(chunk_size)
                if not data:
                    break
                yield data

    upload_response = requests.post('https://api.assemblyai.com/v2/upload',
                                    headers=headers,
                                    data=read_file(filename))
    audio_url = upload_response.json()['upload_url']

    # שלב ב: בקשת תמלול (בעברית)
    print("2. Starting transcription (Language: Hebrew)...")
    endpoint = "https://api.assemblyai.com/v2/transcript"
    json = {
        "audio_url": audio_url,
        "language_code": "he"  # הגדרה קריטית לעברית
    }
    response = requests.post(endpoint, json=json, headers=headers)
    transcript_id = response.json()['id']

    # שלב ג: המתנה לסיום (Polling)
    print("3. Processing... (waiting for completion)")
    while True:
        polling_endpoint = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"
        polling_response = requests.get(polling_endpoint, headers=headers)
        status = polling_response.json()['status']

        if status == 'completed':
            return polling_response.json()['text']
        elif status == 'error':
            raise Exception("Transcription failed: " + polling_response.json()['error'])

        time.sleep(3)  # בדיקה כל 3 שניות


# --- פונקציה 2: ניתוח ויצירת כרטיסיות עם GPT-4o ---
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


# --- הריצה הראשית ---
if __name__ == "__main__":
    try:
        # בדיקה שהקובץ קיים
        if not os.path.exists(AUDIO_FILENAME):
            print(f"Error: File '{AUDIO_FILENAME}' not found. Please add an audio file.")
            exit()

        # שלב 1: תמלול
        hebrew_text = transcribe_audio(AUDIO_FILENAME)
        print("\n--- Raw Transcript (First 200 chars) ---")
        print(hebrew_text[:200] + "...")

        # שלב 2: יצירת חומרי לימוד
        study_material = generate_study_material(hebrew_text)

        print("\n" + "=" * 50)
        print(" FINAL OUTPUT (Summary & Flashcards) ")
        print("=" * 50)
        print(study_material)
        print("=" * 50)

    except Exception as e:
        print(f"\nError occurred: {e}")