from typing import Optional

from sqlmodel import Field, SQLModel


class Lecture(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    title: str
    filename: str
    status: str
    transcript: str = ""
    words_json: str = "[]"
    processed_content_json: str = "{}"
    assemblyai_transcript_id: Optional[str] = None
    # boosting_audio, uploading, transcribing, refining_transcript,
    # generating_study_material, completed
    processing_stage: Optional[str] = None
    progress_percent: Optional[int] = None
    validation_json: Optional[str] = None
    # Panopto pilot integration (see app/services/panopto_service.py). Set when this
    # lecture was created from a Panopto recording, so the finished captions can be
    # pushed back onto that same session.
    # Unique: it's what "have we already ingested this recording?" is decided on, and
    # the poller can have two runs in flight at once (see the sync workflow), which
    # without this could each pass that check for the same session and ingest it
    # twice — two downloads, two transcriptions billed, and a duplicate caption push
    # that Panopto then rejects. NULLs aren't constrained, so non-Panopto lectures
    # are unaffected.
    panopto_session_id: Optional[str] = Field(default=None, unique=True, index=True)
    panopto_captions_synced_at: Optional[str] = None
    panopto_sync_error: Optional[str] = None
