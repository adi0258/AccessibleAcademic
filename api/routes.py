import os
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select

from core.database import get_session
from models.lecture import Lecture
from services.pdf_service import export_lecture_pdf
from services.pipeline_service import run_full_pipeline
from services.subtitle_service import export_lecture_srt


router = APIRouter()
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@router.post("/upload")
def upload_audio(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")
    safe_name = os.path.basename(file.filename)
    file_path = os.path.join(BASE_DIR, "recordings", safe_name)
    try:
        contents = file.file.read()
        with open(file_path, "wb") as f:
            f.write(contents)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"filename": safe_name}


@router.post("/process")
def process_lecture(
    title: str,
    filename: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    file_path = os.path.join(BASE_DIR, "recordings", filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    new_lecture = Lecture(
        title=title,
        filename=filename,
        status="processing",
        processing_stage="pending",
        progress_percent=0,
    )
    session.add(new_lecture)
    session.commit()
    session.refresh(new_lecture)

    background_tasks.add_task(run_full_pipeline, new_lecture.id, file_path)
    return {"message": "Started", "lecture_id": new_lecture.id}


@router.get("/lectures", response_model=List[Lecture])
def get_all_lectures(session: Session = Depends(get_session)):
    return session.exec(select(Lecture)).all()


@router.get("/lectures/{lecture_id}", response_model=Lecture)
def get_lecture(lecture_id: int, session: Session = Depends(get_session)):
    lecture = session.get(Lecture, lecture_id)
    if not lecture:
        raise HTTPException(status_code=404, detail="Lecture not found")
    return lecture


@router.delete("/lectures/{lecture_id}")
def delete_lecture(lecture_id: int, session: Session = Depends(get_session)):
    lecture = session.get(Lecture, lecture_id)
    if not lecture:
        raise HTTPException(status_code=404, detail="Lecture not found")
    session.delete(lecture)
    session.commit()
    return {"message": "Deleted"}


@router.get("/lectures/{lecture_id}/export")
def export_lecture(lecture_id: int, session: Session = Depends(get_session)):
    return export_lecture_pdf(lecture_id, session, BASE_DIR)


@router.get("/lectures/{lecture_id}/export-srt")
def export_lecture_subtitles(lecture_id: int, session: Session = Depends(get_session)):
    return export_lecture_srt(lecture_id, session, BASE_DIR)
