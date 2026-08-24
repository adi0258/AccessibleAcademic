from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

from app.core.config import DATABASE_URL
from app.models import Lecture, PanoptoToken, User


_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def create_db_and_tables() -> None:
    # Import keeps SQLModel metadata registered before create_all runs.
    _ = Lecture, PanoptoToken, User
    SQLModel.metadata.create_all(engine)

    # Add columns that may be missing on a database created before they
    # existed. create_all() (above) only creates missing *tables* — it never
    # alters an existing table to add new columns, on any backend — so this
    # runs unconditionally, not just for SQLite: production uses Postgres
    # (see psycopg2-binary in requirements.txt), and a Postgres-only lecture
    # table missing these would break every query against it, not just
    # Panopto-related ones, since the ORM selects all mapped columns.
    from sqlalchemy import inspect, text

    backfill = {
        "lecture": [
            ("assemblyai_transcript_id", "TEXT"),
            ("processing_stage", "TEXT"),
            ("progress_percent", "INTEGER"),
            ("validation_json", "TEXT"),
            ("user_id", "INTEGER"),
            ("panopto_session_id", "TEXT"),
            ("panopto_captions_synced_at", "TEXT"),
            ("panopto_sync_error", "TEXT"),
        ],
        "panoptotoken": [
            ("access_token", "TEXT"),
            # DOUBLE PRECISION is the float spelling both SQLite and Postgres accept.
            ("access_token_expires_at", "DOUBLE PRECISION"),
        ],
    }

    with engine.connect() as conn:
        inspector = inspect(conn)
        existing_tables = set(inspector.get_table_names())
        for table, columns in backfill.items():
            if table not in existing_tables:
                continue  # create_all() just made it, already has every column
            cols = {c["name"] for c in inspector.get_columns(table)}
            for col, spec in columns:
                if col not in cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {spec}"))

        # Same story for indexes: create_all() won't add one to a table that
        # already exists. Name matches what SQLModel's unique+index Field would
        # have generated, so the two can't end up duplicating each other.
        if "lecture" in existing_tables:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_lecture_panopto_session_id "
                    "ON lecture (panopto_session_id)"
                )
            )
        conn.commit()
