import json
import os
from typing import Optional

from sqlmodel import Session

from app.core.config import EXPORTS_DIR, RECORDINGS_DIR
from app.core.database import engine
from app.models import Lecture
from app.services.ai_service import generate_study_material, transcribe_audio
from app.services.audio_service import boost_audio
from app.services.blob_service import upload_file_to_r2
from app.services.math_utils import clean_study_content
from app.services.subtitle_service import generate_vtt_string
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
        final_content = validation.purified_content
        if not final_content:
            try:
                parsed = json.loads(processed)
            except (TypeError, ValueError):
                parsed = None
            final_content = parsed if isinstance(parsed, dict) else None

        # Models like to punctuate prose with \newline between two formulas; it
        # renders as literal text in the browser and as garbage in the exports.
        final_content_json = (
            json.dumps(clean_study_content(final_content), ensure_ascii=False)
            if final_content
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

        _push_captions_to_panopto_if_linked(lecture_id)
    except Exception as e:
        print(f"Pipeline Error: {str(e)}")
        with Session(engine) as session_error:
            lecture = session_error.get(Lecture, lecture_id)
            if lecture:
                lecture.status = f"error: {str(e)}"
                lecture.processing_stage = "error"
                session_error.add(lecture)
                session_error.commit()
    finally:
        _cleanup_panopto_media(lecture_id, audio_filename)


def _cleanup_panopto_media(lecture_id: int, audio_filename: str) -> None:
    """Delete the working copies of a Panopto-sourced recording once we're done
    with it, success or failure.

    Serverless /tmp is small (512MB) and shared by every invocation that lands
    on the same warm instance, while each lecture leaves two files behind: the
    download and FFmpeg's boosted copy, together roughly twice the source
    video. Left alone, a couple of hour-long lectures fill it and the next
    download fails with no obvious cause — the kind of fault that only shows
    up after days of unattended running.

    Only for Panopto-sourced lectures: those play back from Panopto, so
    nothing needs the local file afterwards. A manually uploaded lecture is
    left alone, since the app itself may still be serving it from /static.
    """
    try:
        with Session(engine) as session:
            lecture = session.get(Lecture, lecture_id)
            if not lecture or not lecture.panopto_session_id:
                return

        base = os.path.basename(audio_filename)
        for name in (base, f"boosted_{base}"):
            path = RECORDINGS_DIR / name
            try:
                if path.is_file():
                    path.unlink()
            except OSError as e:
                print(f"Panopto media cleanup: could not remove {path}: {e}")
    except Exception as e:  # noqa: BLE001 — cleanup must never mask the real outcome
        print(f"Panopto media cleanup failed for lecture {lecture_id}: {e}")


def _push_captions_to_panopto_if_linked(lecture_id: int) -> None:
    """Panopto pilot integration: if this lecture was ingested from a Panopto
    recording (panopto_session_id set), push its VTT back as a caption track
    on that same session. Best-effort — a failure here doesn't affect the
    lecture's own "completed" status, it's only recorded on the lecture row
    for the sync status to surface (see panopto_service, /panopto/status)."""
    with Session(engine) as session:
        lecture = session.get(Lecture, lecture_id)
        if not lecture or not lecture.panopto_session_id:
            return
        session_id = lecture.panopto_session_id
        vtt_content = generate_vtt_string(lecture.words_json)

    if not vtt_content:
        return

    from datetime import datetime, timezone

    from app.services import panopto_service

    vtt_path = EXPORTS_DIR / f"panopto_push_{lecture_id}.vtt"
    try:
        with open(vtt_path, "w", encoding="utf-8") as f:
            f.write(vtt_content)
        panopto_service.upload_captions(session_id, str(vtt_path))
        synced_at = datetime.now(timezone.utc).isoformat()
        error = None
    except Exception as e:
        synced_at = None
        error = str(e)
        print(f"Panopto caption push failed for lecture {lecture_id}: {error}")
    finally:
        try:
            os.remove(vtt_path)
        except OSError:
            pass

    with Session(engine) as session:
        lecture = session.get(Lecture, lecture_id)
        if lecture:
            lecture.panopto_captions_synced_at = synced_at
            lecture.panopto_sync_error = error
            session.add(lecture)
            session.commit()
