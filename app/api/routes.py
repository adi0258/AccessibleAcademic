from pathlib import Path
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select

from app.core.config import RECORDINGS_DIR
from app.core.database import get_session
from app.models import Lecture
from app.services.blob_service import delete_blob, generate_client_upload_token
from app.services.pdf_service import export_lecture_pdf
from app.services.pipeline_service import run_full_pipeline
from app.services.subtitle_service import export_lecture_srt, export_lecture_vtt
from app.services.word_service import export_lecture_docx


router = APIRouter()


@router.post("/upload")
def upload_audio(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")

    safe_name = Path(file.filename).name
    file_path = RECORDINGS_DIR / safe_name

    try:
        contents = file.file.read()
        with open(file_path, "wb") as file_obj:
            file_obj.write(contents)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"filename": safe_name}


@router.get("/upload-token")
def get_upload_token(filename: str):
    try:
        return generate_client_upload_token(filename)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/process")
def process_lecture(
    title: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    filename: str = "",
    blob_url: str = "",
):
    if blob_url:
        audio_source = blob_url
        stored_filename = blob_url
    elif filename:
        file_path = RECORDINGS_DIR / filename
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found")
        audio_source = str(file_path)
        stored_filename = filename
    else:
        raise HTTPException(status_code=400, detail="Either filename or blob_url required")

    new_lecture = Lecture(
        title=title,
        filename=stored_filename,
        status="processing",
        processing_stage="pending",
        progress_percent=0,
    )
    session.add(new_lecture)
    session.commit()
    session.refresh(new_lecture)

    background_tasks.add_task(run_full_pipeline, new_lecture.id, audio_source)
    return {"message": "Started", "lecture_id": new_lecture.id}


@router.get("/debug-env")
def debug_env():
    import os, sys, shutil, sysconfig, glob
    scripts_dir = sysconfig.get_path("scripts")
    search_results = glob.glob("/var/**/node", recursive=True)[:5] + \
                     glob.glob("/usr/**/node", recursive=True)[:3] + \
                     glob.glob("/home/**/node", recursive=True)[:3]
    return {
        "VERCEL": os.environ.get("VERCEL"),
        "python_exe": sys.executable,
        "python_bin_dir": os.path.dirname(sys.executable),
        "sysconfig_scripts": scripts_dir,
        "node_in_scripts_dir": os.path.exists(os.path.join(scripts_dir or "", "node")),
        "system_node": shutil.which("node"),
        "node_found_by_glob": search_results,
    }


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
    # Clean up blob storage if the filename is a remote URL
    if lecture.filename and lecture.filename.startswith("http"):
        delete_blob(lecture.filename)
    session.delete(lecture)
    session.commit()
    return {"message": "Deleted"}


@router.get("/lectures/{lecture_id}/export")
def export_lecture(lecture_id: int, session: Session = Depends(get_session)):
    return export_lecture_pdf(lecture_id, session)


@router.get("/lectures/{lecture_id}/export-docx")
def export_lecture_docx_file(lecture_id: int, session: Session = Depends(get_session)):
    return export_lecture_docx(lecture_id, session)


@router.get("/lectures/{lecture_id}/export-srt")
def export_lecture_subtitles(lecture_id: int, session: Session = Depends(get_session)):
    return export_lecture_srt(lecture_id, session)


@router.get("/lectures/{lecture_id}/export-vtt")
def export_lecture_subtitles_vtt(lecture_id: int, session: Session = Depends(get_session)):
    return export_lecture_vtt(lecture_id, session)


@router.get("/lectures/{lecture_id}/export-vvt")
def export_lecture_subtitles_vvt_alias(lecture_id: int, session: Session = Depends(get_session)):
    # Backward-compatible alias for common typo ("vvt" instead of "vtt")
    return export_lecture_vtt(lecture_id, session)


@router.get("/lectures/{lecture_id}/validation")
def get_lecture_validation(lecture_id: int, session: Session = Depends(get_session)):
    """Return the grounding validation report for a lecture."""
    lecture = session.get(Lecture, lecture_id)
    if not lecture:
        raise HTTPException(status_code=404, detail="Lecture not found")
    if not lecture.validation_json:
        raise HTTPException(status_code=404, detail="Validation report not available for this lecture")
    import json as _json
    return _json.loads(lecture.validation_json)
