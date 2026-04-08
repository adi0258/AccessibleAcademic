from sqlmodel import Session, SQLModel, create_engine


sqlite_url = "sqlite:///./database.db"
engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})


def get_session():
    with Session(engine) as session:
        yield session


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    # Add progress columns if they don't exist (for existing DBs)
    from sqlalchemy import text

    with engine.connect() as conn:
        r = conn.execute(text("PRAGMA table_info(lecture)"))
        cols = {row[1] for row in r}
        for col, spec in [
            ("assemblyai_transcript_id", "TEXT"),
            ("processing_stage", "TEXT"),
            ("progress_percent", "INTEGER"),
        ]:
            if col not in cols:
                conn.execute(text(f"ALTER TABLE lecture ADD COLUMN {col} {spec}"))
        conn.commit()
