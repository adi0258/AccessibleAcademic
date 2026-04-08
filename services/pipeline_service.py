import json
import os
from typing import Optional

from sqlmodel import Session

from core.database import engine
from models.lecture import Lecture
from services.ai_service import generate_study_material, refine_transcript, transcribe_audio
from services.audio_service import boost_audio


def _update_lecture_progress(
    lecture_id: int,
    processing_stage: str,
    progress_percent: int,
    assemblyai_transcript_id: Optional[str] = None,
):
    with Session(engine) as session:
        lecture = session.get(Lecture, lecture_id)
        if lecture:
            lecture.processing_stage = processing_stage
            lecture.progress_percent = progress_percent
            if assemblyai_transcript_id is not None:
                lecture.assemblyai_transcript_id = assemblyai_transcript_id
            session.add(lecture)
            session.commit()


def run_full_pipeline(lecture_id: int, audio_filename: str):
    # Background task: always open fresh sessions to avoid detached instance/thread issues.
    with Session(engine) as session:
        lecture = session.get(Lecture, lecture_id)
        if not lecture:
            return

    try:
        _update_lecture_progress(lecture_id, "boosting_audio", 5)
        boosted_path = boost_audio(audio_filename)

        with Session(engine) as session_boosted:
            lec = session_boosted.get(Lecture, lecture_id)
            if lec:
                lec.filename = os.path.basename(boosted_path)
                session_boosted.add(lec)
                session_boosted.commit()

        result = transcribe_audio(boosted_path, lecture_id=lecture_id, progress_cb=_update_lecture_progress)

        _update_lecture_progress(lecture_id, "refining_transcript", 75)
        refined_text = refine_transcript(result["text"])

        with Session(engine) as session2:
            lec = session2.get(Lecture, lecture_id)
            if lec:
                lec.transcript = refined_text
                lec.words_json = json.dumps(result.get("words", []))
                session2.add(lec)
                session2.commit()

        _update_lecture_progress(lecture_id, "generating_study_material", 90)
        processed = generate_study_material(refined_text)
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
