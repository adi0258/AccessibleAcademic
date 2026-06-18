import json
import os
from typing import Optional

from sqlmodel import Session

from app.core.database import engine
from app.models import Lecture
from app.services.ai_service import generate_study_material, transcribe_audio
from app.services.audio_service import boost_audio
from app.services.blob_service import upload_file_to_r2
from app.services.validation_service import validate_study_material


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

    is_url = audio_filename.startswith("http://") or audio_filename.startswith("https://")

    try:
        if is_url:
            # File is already in Cloudflare R2 — skip FFmpeg boost and AssemblyAI upload
            _update_lecture_progress(lecture_id, "transcribing", 10)
            audio_source = audio_filename
        else:
            _update_lecture_progress(lecture_id, "boosting_audio", 5)
            boosted_path = boost_audio(audio_filename)

            with Session(engine) as session_boosted:
                lecture = session_boosted.get(Lecture, lecture_id)
                if lecture:
                    lecture.filename = os.path.basename(boosted_path)
                    session_boosted.add(lecture)
                    session_boosted.commit()

            audio_source = boosted_path

        result = transcribe_audio(audio_source, lecture_id=lecture_id, progress_cb=_update_lecture_progress)

        _update_lecture_progress(lecture_id, "transcription_completed", 75)
        transcript_text = result["text"]

        with Session(engine) as session_transcript:
            lecture = session_transcript.get(Lecture, lecture_id)
            if lecture:
                lecture.transcript = transcript_text
                lecture.words_json = json.dumps(result.get("words", []))
                session_transcript.add(lecture)
                session_transcript.commit()

        _update_lecture_progress(lecture_id, "generating_study_material", 90)
        processed = generate_study_material(transcript_text)

        _update_lecture_progress(lecture_id, "validating_content", 95)
        validation = validate_study_material(transcript_text, processed)

        # Use purified content if the validator produced one; fall back to raw output
        final_content_json = (
            json.dumps(validation.purified_content, ensure_ascii=False)
            if validation.purified_content
            else processed
        )

        with Session(engine) as session_processed:
            lecture = session_processed.get(Lecture, lecture_id)
            if lecture:
                lecture.processed_content_json = final_content_json
                lecture.validation_json = validation.to_json()
                lecture.status = "completed"
                lecture.processing_stage = "completed"
                lecture.progress_percent = 100
                session_processed.add(lecture)
                session_processed.commit()
    except Exception as e:
        print(f"Pipeline Error: {str(e)}")
        with Session(engine) as session_error:
            lecture = session_error.get(Lecture, lecture_id)
            if lecture:
                lecture.status = f"error: {str(e)}"
                lecture.processing_stage = "error"
                session_error.add(lecture)
                session_error.commit()
