from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

from app.core.config import DATABASE_URL
from app.models import Lecture


_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def create_db_and_tables() -> None:
    # Import keeps SQLModel metadata registered before create_all runs.
    _ = Lecture
    SQLModel.metadata.create_all(engine)

    # SQLite only: add columns that may be missing in older local databases.
    if DATABASE_URL.startswith("sqlite"):
        from sqlalchemy import text
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(lecture)"))
            cols = {row[1] for row in result}
            for col, spec in [
                ("assemblyai_transcript_id", "TEXT"),
                ("processing_stage", "TEXT"),
                ("progress_percent", "INTEGER"),
            ]:
                if col not in cols:
                    conn.execute(text(f"ALTER TABLE lecture ADD COLUMN {col} {spec}"))
            conn.commit()
