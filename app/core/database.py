from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

from app.core.config import DATABASE_URL
from app.models import Lecture, User


_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def create_db_and_tables() -> None:
    # Import keeps SQLModel metadata registered before create_all runs.
    _ = Lecture, User
    SQLModel.metadata.create_all(engine)

    # Add columns that may be missing on a database created before they
    # existed. create_all() (above) only creates missing *tables* — it never
    # alters an existing table to add new columns, on any backend — so this
    # runs unconditionally, not just for SQLite: production uses Postgres
    # (see psycopg2-binary in requirements.txt), and a Postgres-only lecture
    # table missing these would break every query against it, not just
    # Panopto-related ones, since the ORM selects all mapped columns.
    from sqlalchemy import inspect, text

    with engine.connect() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("lecture")}
        for col, spec in [
            ("assemblyai_transcript_id", "TEXT"),
            ("processing_stage", "TEXT"),
            ("progress_percent", "INTEGER"),
            ("validation_json", "TEXT"),
            ("user_id", "INTEGER"),
            ("panopto_session_id", "TEXT"),
            ("panopto_captions_synced_at", "TEXT"),
            ("panopto_sync_error", "TEXT"),
        ]:
            if col not in cols:
                conn.execute(text(f"ALTER TABLE lecture ADD COLUMN {col} {spec}"))
        conn.commit()
