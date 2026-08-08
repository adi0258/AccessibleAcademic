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
