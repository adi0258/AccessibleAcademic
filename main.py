import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routes import router as api_router
from core.database import create_db_and_tables


load_dotenv()

app = FastAPI(title="Accessible Academic Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RECORDINGS_DIR = os.path.join(BASE_DIR, "recordings")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

if not os.path.exists(RECORDINGS_DIR):
    os.makedirs(RECORDINGS_DIR)

app.mount("/static", StaticFiles(directory=RECORDINGS_DIR), name="static")
if os.path.exists(ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

app.include_router(api_router)


@app.on_event("startup")
def on_startup():
    create_db_and_tables()


@app.get("/", response_class=FileResponse)
def serve_ui():
    return FileResponse(os.path.join(BASE_DIR, "index.html"))


@app.get("/watch/{lecture_id}", response_class=FileResponse)
def watch_lecture(lecture_id: int):
    return FileResponse(os.path.join(BASE_DIR, "watch.html"))