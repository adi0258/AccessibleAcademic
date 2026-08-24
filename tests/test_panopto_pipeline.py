"""
Regression suite for the Panopto pilot pipeline.

Standalone on purpose — plain asserts, no test framework, so it needs nothing
that isn't already in requirements.txt and runs anywhere the app runs:

    python3 tests/test_panopto_pipeline.py

Everything here is hermetic: a throwaway SQLite database per section and
mocked HTTP. It never touches Panopto, OpenAI, AssemblyAI, the production
database, or the live OAuth credentials.

Each section is written against a failure that actually happened, or one the
audit showed was reachable — the comments say which, because a test whose
purpose is forgotten is a test that gets "fixed" by deleting it.
"""

import os
import sys
import tempfile
import time
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASSED, FAILED = [], []


def check(name, fn):
    try:
        fn()
        PASSED.append(name)
        print(f"  PASS  {name}")
    except Exception as e:  # noqa: BLE001
        FAILED.append((name, e))
        print(f"  FAIL  {name}: {e}")


def fresh_db(**env):
    """Point the app at an empty database and reload the modules that bind to
    it at import time."""
    path = tempfile.mktemp(suffix=".db")
    os.environ["DATABASE_URL"] = f"sqlite:///{path}"
    os.environ.setdefault("PANOPTO_BASE_URL", "https://example.panopto.eu")
    os.environ.setdefault("PANOPTO_CLIENT_ID", "cid")
    os.environ.setdefault("PANOPTO_CLIENT_SECRET", "csec")
    os.environ["PANOPTO_REFRESH_TOKEN"] = env.pop("PANOPTO_REFRESH_TOKEN", "")
    for k, v in env.items():
        os.environ[k] = str(v)

    _reset_app_modules()
    from app.core.database import create_db_and_tables

    create_db_and_tables()
    return path


def _reset_app_modules():
    """Drop the app modules so they re-bind to the new DATABASE_URL.

    SQLModel's metadata is global and survives the reload, so re-importing the
    models would try to define the same tables again — clear it first.
    """
    import sqlmodel

    for mod in [m for m in list(sys.modules) if m.startswith("app.")]:
        del sys.modules[mod]
    sqlmodel.SQLModel.metadata.clear()


def ok_token(access, refresh=None, expires_in=3600):
    payload = {"access_token": access, "expires_in": expires_in}
    if refresh:
        payload["refresh_token"] = refresh
    return MagicMock(ok=True, json=lambda: payload)


# ---------------------------------------------------------------------------
# OAuth token lifecycle
# ---------------------------------------------------------------------------

def test_token_lifecycle():
    fresh_db(PANOPTO_REFRESH_TOKEN="env-seed")
    import app.services.panopto_service as ps

    def reset():
        ps._token_cache.update(value=None, expires_at=0.0)

    # Bootstraps from the env seed exactly once, then persists the rotation.
    reset()
    with patch.object(ps.requests, "post") as m:
        m.return_value = ok_token("AT1", "R1")
        assert ps.get_access_token() == "AT1"
        assert m.call_args.kwargs["data"]["refresh_token"] == "env-seed"
    assert ps._load_token_row()["refresh_token"] == "R1"

    # The one that matters: a cold process (empty memory, as every serverless
    # invocation is) must reuse the cached access token rather than spend the
    # refresh token again. Spending it per-poll is what made overlapping polls
    # race and left one holding a dead credential.
    reset()
    with patch.object(ps.requests, "post") as m:
        for _ in range(5):
            assert ps.get_access_token() == "AT1"
        assert m.call_count == 0, f"{m.call_count} needless token exchanges"

    # Expiry is taken from Panopto's expires_in, with headroom so a token
    # can't lapse between the check and the call that uses it.
    ps._save_token_state("R1", access_token="stale", access_token_expires_at=0.0)
    reset()
    with patch.object(ps.requests, "post") as m:
        m.return_value = ok_token("AT2", "R2", expires_in=300)
        t0 = time.time()
        assert ps.get_access_token() == "AT2"
    assert abs(ps._load_token_row()["access_token_expires_at"] - (t0 + 300 - 60)) < 2

    # A transient failure must not destroy the stored credential.
    ps._save_token_state("R2", access_token="", access_token_expires_at=0.0)
    reset()
    with patch.object(ps.requests, "post") as m:
        m.return_value = MagicMock(ok=False, status_code=503, text="unavailable")
        try:
            ps.get_access_token()
            raise AssertionError("expected PanoptoAPIError")
        except ps.PanoptoAPIError:
            pass
        assert m.call_count == 1, "refresh loop — should be one attempt per call"
    assert ps._load_token_row()["refresh_token"] == "R2"


def test_refresh_token_rotation_is_atomic():
    """A slow writer must not bury a newer refresh token under an older one.

    Read-modify-write through the ORM allowed exactly that; the fix is a
    conditional UPDATE, so a late writer affects zero rows instead.
    """
    fresh_db()
    import app.services.panopto_service as ps

    ps._save_token_state("R_new", access_token="AT", access_token_expires_at=time.time() + 3000)
    assert ps._save_token_state("R_stale", expected_refresh_token="R_old") is False
    assert ps._load_token_row()["refresh_token"] == "R_new"
    assert ps._save_token_state("R_next", expected_refresh_token="R_new") is True
    assert ps._load_token_row()["refresh_token"] == "R_next"

    # End to end: a competitor rotates mid-exchange. We still return a usable
    # token, and theirs survives.
    ps._save_token_state("R1", access_token="old", access_token_expires_at=time.time() - 1)
    ps._token_cache.update(value=None, expires_at=0.0)

    def racing(_rt):
        ps._save_token_state("R_winner", access_token="AT_win",
                             access_token_expires_at=time.time() + 3000)
        return {"access_token": "AT_lose", "refresh_token": "R_lose", "expires_in": 3600}

    with patch.object(ps, "_exchange_refresh_token", side_effect=racing):
        assert ps.get_access_token() == "AT_lose"
    assert ps._load_token_row()["refresh_token"] == "R_winner"


def test_stale_access_token_recovers():
    """A token Panopto rejects before its stated expiry used to stick in the
    cache for up to an hour, quietly 401-ing everything."""
    fresh_db()
    import app.services.panopto_service as ps

    ps._save_token_state("R", access_token="AT_stale", access_token_expires_at=time.time() + 3000)
    ps._token_cache.update(value=None, expires_at=0.0)
    calls = []

    def fake(method, url, headers=None, **kw):
        calls.append(headers["Authorization"])
        if headers["Authorization"].endswith("AT_stale"):
            return MagicMock(ok=False, status_code=401, text="unauthorized")
        return MagicMock(ok=True, status_code=200, json=lambda: {"Results": [{"Id": "s1", "Name": "L"}]})

    with patch.object(ps.requests, "request", side_effect=fake), \
         patch.object(ps.requests, "post") as post:
        post.return_value = ok_token("AT_fresh", "R2")
        assert ps.list_recent_sessions("f")[0]["Id"] == "s1"
    assert len(calls) == 2, "expected reject-then-retry"
    assert ps._load_token_row()["access_token"] == "AT_fresh"

    # A genuine, persistent 401 must stay bounded rather than spin.
    ps._save_token_state("R2", access_token="", access_token_expires_at=0.0)
    ps._token_cache.update(value=None, expires_at=0.0)
    n = []
    with patch.object(ps.requests, "request",
                      side_effect=lambda *a, **k: (n.append(1), MagicMock(ok=False, status_code=401, text="no"))[1]), \
         patch.object(ps.requests, "post") as post:
        post.return_value = ok_token("AT_x", "R3")
        try:
            ps.list_recent_sessions("f")
        except ps.PanoptoAPIError:
            pass
    assert len(n) <= 6, f"unbounded retry: {len(n)} calls"


# ---------------------------------------------------------------------------
# Ingest state machine
# ---------------------------------------------------------------------------

def _seed_user(engine):
    from sqlmodel import Session
    from app.models import User
    with Session(engine) as db:
        db.add(User(google_sub="s", email="a@b.c"))
        db.commit()
    return 1


def test_claim_prevents_duplicate_ingest():
    """Two poller runs overlap constantly by design. Both claiming the same
    recording would mean two downloads and two transcriptions billed."""
    fresh_db(PANOPTO_MAX_NEW_PER_SYNC=5)
    from sqlmodel import Session, select
    from app.core.database import engine
    from app.models import Lecture
    import app.services.panopto_service as ps

    uid = _seed_user(engine)
    sessions = [{"Id": "sess-A", "Name": "A"}]

    with patch.object(ps, "list_recent_sessions", return_value=sessions):
        with Session(engine) as db:
            first = ps.discover_and_ingest(db, uid, folder_id="f")
        with Session(engine) as db:
            second = ps.discover_and_ingest(db, uid, folder_id="f")

    assert len(first["created"]) == 1, first
    assert second["created"] == [] and second["skipped"] == ["sess-A"], second
    with Session(engine) as db:
        rows = db.exec(select(Lecture).where(Lecture.panopto_session_id == "sess-A")).all()
    assert len(rows) == 1
    # Claimed before downloading, so a competing run sees it immediately.
    assert rows[0].processing_stage == "downloading"
    assert rows[0].ingest_attempts == 1


def test_backlog_is_rate_limited():
    """A pile of new recordings must not all be taken on at once — that put
    minutes of downloads into one invocation and got it killed partway,
    leaving rows created but never processed."""
    fresh_db(PANOPTO_MAX_NEW_PER_SYNC=1)
    from sqlmodel import Session
    from app.core.database import engine
    import app.services.panopto_service as ps

    uid = _seed_user(engine)
    many = [{"Id": f"s{i}", "Name": f"L{i}"} for i in range(20)]
    with patch.object(ps, "list_recent_sessions", return_value=many):
        with Session(engine) as db:
            r = ps.discover_and_ingest(db, uid, folder_id="f")
    assert len(r["created"]) == 1, f"claimed {len(r['created'])}, expected 1"
    # The rest are queued for later polls, not silently ignored.
    assert len(r["deferred"]) == 19, r["deferred"]


def test_stalled_lecture_is_reaped_and_retried():
    """A killed invocation leaves a row saying 'processing' forever, which
    every later poll reads as 'someone is on it'. The recording is then never
    retried and never reported as failed."""
    fresh_db(PANOPTO_STALL_MINUTES=30, PANOPTO_MAX_NEW_PER_SYNC=5)
    from datetime import datetime, timedelta, timezone
    from sqlmodel import Session
    from app.core.database import engine
    from app.models import Lecture
    import app.services.panopto_service as ps

    uid = _seed_user(engine)
    old = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()
    with Session(engine) as db:
        db.add(Lecture(title="stuck", filename="x.mp4", status="processing",
                       processing_stage="transcribing", user_id=uid,
                       panopto_session_id="sess-S", ingest_attempts=1,
                       last_progress_at=old))
        db.commit()

    with patch.object(ps, "list_recent_sessions", return_value=[{"Id": "sess-S", "Name": "S"}]):
        with Session(engine) as db:
            r = ps.discover_and_ingest(db, uid, folder_id="f")

    assert len(r["reaped"]) == 1, r
    assert len(r["created"]) == 1, "reaped lecture should become claimable again"
    with Session(engine) as db:
        row = db.exec(__import__("sqlmodel").select(Lecture)).first()
    assert row.ingest_attempts == 2

    # A lecture that IS still making progress must not be reaped.
    with Session(engine) as db:
        row = db.exec(__import__("sqlmodel").select(Lecture)).first()
        row.status = "processing"
        row.last_progress_at = datetime.now(timezone.utc).isoformat()
        db.add(row)
        db.commit()
    with Session(engine) as db:
        assert ps.reap_stalled_lectures(db) == []


def test_retries_are_bounded():
    """Retrying forever means re-downloading a broken recording every couple
    of minutes for as long as it exists."""
    fresh_db(PANOPTO_MAX_INGEST_ATTEMPTS=3, PANOPTO_MAX_NEW_PER_SYNC=5)
    from sqlmodel import Session, select
    from app.core.database import engine
    from app.models import Lecture
    import app.services.panopto_service as ps

    uid = _seed_user(engine)
    with Session(engine) as db:
        db.add(Lecture(title="bad", filename="", status="error: nope",
                       processing_stage="download_failed", user_id=uid,
                       panopto_session_id="sess-B", ingest_attempts=3))
        db.commit()

    with patch.object(ps, "list_recent_sessions", return_value=[{"Id": "sess-B", "Name": "B"}]):
        with Session(engine) as db:
            r = ps.discover_and_ingest(db, uid, folder_id="f")
    assert r["created"] == [], "should have stopped retrying"
    assert len(r["gave_up"]) == 1 and r["gave_up"][0]["attempts"] == 3

    # Below the ceiling it still retries.
    with Session(engine) as db:
        row = db.exec(select(Lecture)).first()
        row.ingest_attempts = 1
        db.add(row)
        db.commit()
    with patch.object(ps, "list_recent_sessions", return_value=[{"Id": "sess-B", "Name": "B"}]):
        with Session(engine) as db:
            r = ps.discover_and_ingest(db, uid, folder_id="f")
    assert len(r["created"]) == 1 and r["created"][0]["retry"] is True


def test_failed_download_is_recorded_and_retryable():
    fresh_db(PANOPTO_MAX_NEW_PER_SYNC=5)
    from sqlmodel import Session, select
    from app.core.database import engine
    from app.models import Lecture
    import app.services.panopto_service as ps

    uid = _seed_user(engine)
    with patch.object(ps, "list_recent_sessions", return_value=[{"Id": "sess-D", "Name": "D"}]):
        with Session(engine) as db:
            r = ps.discover_and_ingest(db, uid, folder_id="f")
    lecture_id = r["created"][0]["lecture_id"]

    with patch.object(ps, "download_session_video", side_effect=RuntimeError("no download url")):
        ps.ingest_and_process(lecture_id, "sess-D")

    with Session(engine) as db:
        row = db.get(Lecture, lecture_id)
    assert row.status.startswith("error: download failed")
    assert row.processing_stage == "download_failed"
    assert ps._is_retryable_failure(row) is True


# ---------------------------------------------------------------------------
# Caption push
# ---------------------------------------------------------------------------

def test_caption_push_retry_and_reconcile():
    """Two separate holes: a transient push failure was terminal, and a push
    that succeeded while its bookkeeping failed looked identical to one that
    never happened."""
    fresh_db(PANOPTO_MAX_CAPTION_ATTEMPTS=3)
    import json
    from sqlmodel import Session
    from app.core.database import engine
    from app.models import Lecture
    import app.services.panopto_service as ps
    import app.services.pipeline_service as pl

    uid = _seed_user(engine)
    words = json.dumps([{"text": "שלום", "start": 0, "end": 500}])
    with Session(engine) as db:
        db.add(Lecture(title="L", filename="f.mp4", status="completed",
                       processing_stage="completed", progress_percent=100,
                       user_id=uid, panopto_session_id="sess-C", words_json=words))
        db.commit()
        lid = db.exec(__import__("sqlmodel").select(Lecture)).first().id

    # Transient failure -> recorded, still retryable
    with patch.object(ps, "upload_captions", side_effect=RuntimeError("connection reset")):
        assert pl.push_captions_for_lecture(lid) == "failed"
    with Session(engine) as db:
        row = db.get(Lecture, lid)
    assert row.panopto_captions_synced_at is None and row.caption_attempts == 1

    # The poller picks it up again and it succeeds
    with patch.object(ps, "upload_captions", return_value={"ok": True}):
        with Session(engine) as db:
            out = ps.retry_caption_pushes(db)
    assert out and out[0]["outcome"] == "synced", out
    with Session(engine) as db:
        assert db.get(Lecture, lid).panopto_captions_synced_at is not None

    # Reconcile: upload landed, our write didn't. Panopto answers the retry
    # with "already has captions" -> treat as done, not as a permanent error.
    with Session(engine) as db:
        row = db.get(Lecture, lid)
        row.panopto_captions_synced_at = None
        row.caption_attempts = 1
        db.add(row)
        db.commit()
    with patch.object(ps, "upload_captions",
                      side_effect=RuntimeError('400: {"Message":"Session already has captions in this language."}')):
        assert pl.push_captions_for_lecture(lid) == "already_present"
    with Session(engine) as db:
        row = db.get(Lecture, lid)
    assert row.panopto_captions_synced_at is not None and row.panopto_sync_error is None

    # Exhausted attempts drop out of the retry queue rather than looping.
    with Session(engine) as db:
        row = db.get(Lecture, lid)
        row.panopto_captions_synced_at = None
        row.caption_attempts = 3
        db.add(row)
        db.commit()
        assert ps.retry_caption_pushes(db) == []


def test_empty_transcript_is_recorded_not_silent():
    """A silent or seconds-long recording produces no caption cues. That used
    to return quietly, which is indistinguishable from the push never running."""
    fresh_db()
    from sqlmodel import Session, select
    from app.core.database import engine
    from app.models import Lecture
    import app.services.pipeline_service as pl

    uid = _seed_user(engine)
    with Session(engine) as db:
        db.add(Lecture(title="silent", filename="f.mp4", status="completed",
                       user_id=uid, panopto_session_id="sess-E", words_json="[]"))
        db.commit()
        lid = db.exec(select(Lecture)).first().id
    assert pl.push_captions_for_lecture(lid) == "skipped"
    with Session(engine) as db:
        assert "nothing to upload" in db.get(Lecture, lid).panopto_sync_error


# ---------------------------------------------------------------------------
# Media cleanup
# ---------------------------------------------------------------------------

def test_media_cleanup():
    """Serverless /tmp is 512MB and shared; each lecture leaves the download
    plus FFmpeg's boosted copy behind."""
    fresh_db()
    from sqlmodel import Session, select
    from app.core.config import RECORDINGS_DIR
    from app.core.database import engine
    from app.models import Lecture
    import app.services.pipeline_service as pl

    uid = _seed_user(engine)
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    with Session(engine) as db:
        db.add(Lecture(title="p", filename="panopto_X.mp4", status="completed",
                       user_id=uid, panopto_session_id="sess-X"))
        db.add(Lecture(title="m", filename="manual.mp4", status="completed", user_id=uid))
        db.commit()
        rows = db.exec(select(Lecture)).all()
        pan_id, man_id = rows[0].id, rows[1].id

    for n in ("panopto_X.mp4", "boosted_panopto_X.mp4", "manual.mp4"):
        (RECORDINGS_DIR / n).write_bytes(b"x")

    pl._cleanup_panopto_media(pan_id, str(RECORDINGS_DIR / "panopto_X.mp4"))
    assert not (RECORDINGS_DIR / "panopto_X.mp4").exists()
    assert not (RECORDINGS_DIR / "boosted_panopto_X.mp4").exists()

    # A manual upload may still be served from /static — leave it alone.
    pl._cleanup_panopto_media(man_id, str(RECORDINGS_DIR / "manual.mp4"))
    assert (RECORDINGS_DIR / "manual.mp4").exists()
    (RECORDINGS_DIR / "manual.mp4").unlink()

    # Missing files and unknown ids must not raise.
    pl._cleanup_panopto_media(pan_id, str(RECORDINGS_DIR / "gone.mp4"))
    pl._cleanup_panopto_media(999999, str(RECORDINGS_DIR / "gone.mp4"))


# ---------------------------------------------------------------------------
# Transcription resilience
# ---------------------------------------------------------------------------

def test_stale_download_sweep():
    """A worker killed mid-download leaves the partial file behind; nothing
    else ever removes it, and /tmp is 512MB shared across invocations."""
    fresh_db()
    import os as _os
    from app.core.config import RECORDINGS_DIR
    import app.services.panopto_service as ps

    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    old = RECORDINGS_DIR / "panopto_abandoned.mp4"
    fresh = RECORDINGS_DIR / "panopto_inflight.mp4"
    other = RECORDINGS_DIR / "manual_upload.mp4"
    for p in (old, fresh, other):
        p.write_bytes(b"x")
    ancient = time.time() - 10 * 3600
    _os.utime(old, (ancient, ancient))
    _os.utime(other, (ancient, ancient))

    removed = ps.sweep_stale_downloads(max_age_hours=3)
    assert old.name in removed and not old.exists(), "abandoned download not swept"
    assert fresh.exists(), "swept a download that may still be in flight"
    assert other.exists(), "swept a file that isn't ours"
    fresh.unlink()
    other.unlink()


def test_transcription_timeout_and_poll_retry():
    """The poll loop had no ceiling (a wedged transcript span until the whole
    invocation was killed) and no tolerance for a blip (one failed poll threw
    away a transcription already running and already paid for)."""
    fresh_db(TRANSCRIPTION_TIMEOUT_MINUTES=0)
    import app.services.ai_service as ai

    with patch.object(ai.requests, "post") as post, patch.object(ai.requests, "get") as get:
        post.return_value = MagicMock(ok=True, status_code=200,
                                      raise_for_status=lambda: None, json=lambda: {"id": "t1"})
        get.return_value = MagicMock(ok=True, status_code=200,
                                     raise_for_status=lambda: None, json=lambda: {"status": "processing"})
        try:
            ai.transcribe_audio("https://example.com/a.mp3")
            raise AssertionError("expected a timeout")
        except RuntimeError as e:
            assert "did not finish within" in str(e), e

    # Transient poll failures are absorbed, then the job completes.
    fresh_db(TRANSCRIPTION_TIMEOUT_MINUTES=5)
    import app.services.ai_service as ai
    seq = [ai.requests.RequestException("reset"),
           MagicMock(ok=True, status_code=200, raise_for_status=lambda: None,
                     json=lambda: {"status": "completed", "text": "שלום", "words": [{"text": "שלום"}]})]

    def flaky(*a, **k):
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    with patch.object(ai.requests, "post") as post, \
         patch.object(ai.requests, "get", side_effect=flaky), \
         patch.object(ai.time, "sleep", lambda *_: None):
        post.return_value = MagicMock(ok=True, status_code=200,
                                      raise_for_status=lambda: None, json=lambda: {"id": "t1"})
        out = ai.transcribe_audio("https://example.com/a.mp3")
    assert out["text"] == "שלום"


# ---------------------------------------------------------------------------
# Validation resilience
# ---------------------------------------------------------------------------

def test_validation_survives_purifier_failure():
    """The purifier is the optional final polish, and it ran unguarded — an
    exception there discarded a completed, already-paid-for transcription."""
    fresh_db()
    import json
    import app.services.validation_service as vs

    content = {"topics": ["t"],
               "summaries": [{"topic_name": "t", "content": "c"}],
               "flashcards": [{"question": "q", "answer": "a"}]}
    with patch.object(vs, "_validate_summaries_agent", return_value={"summaries": [], "summary_overall_score": 1.0}), \
         patch.object(vs, "_validate_flashcards_agent", return_value={"flashcards": [], "flashcard_overall_score": 1.0}), \
         patch.object(vs, "_score_grounding_agent", return_value={"overall_score": 0.9, "summary_score": 0.9, "flashcard_score": 0.9}), \
         patch.object(vs, "_purify_content_agent", side_effect=RuntimeError("openai exploded")):
        result = vs.validate_study_material("transcript", json.dumps(content))
    assert result.purified_content["summaries"] == content["summaries"], "content was lost"
    assert any(i.field == "purifier" for i in result.issues), "failure not recorded"

    # Malformed model scores must not blow up the arithmetic.
    with patch.object(vs, "_validate_summaries_agent", return_value={}), \
         patch.object(vs, "_validate_flashcards_agent", return_value={}), \
         patch.object(vs, "_score_grounding_agent", return_value={"overall_score": "not-a-number"}), \
         patch.object(vs, "_purify_content_agent", return_value={"summaries": [], "flashcards": [], "items_rewritten": 0, "items_removed": 0}):
        result = vs.validate_study_material("t", json.dumps(content))
    assert 0.0 <= result.overall_grounding_score <= 1.0


# ---------------------------------------------------------------------------
# Migration / schema
# ---------------------------------------------------------------------------

def test_migration_against_old_schema():
    """Production is Postgres and predates several columns; create_all() only
    creates missing tables, never missing columns."""
    import sqlite3
    path = tempfile.mktemp(suffix=".db")
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE lecture (id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT,
          filename TEXT, status TEXT, transcript TEXT, words_json TEXT,
          processed_content_json TEXT, panopto_session_id TEXT);
        CREATE TABLE panoptotoken (id INTEGER PRIMARY KEY, refresh_token TEXT, updated_at TEXT);
        CREATE TABLE user (id INTEGER PRIMARY KEY, google_sub TEXT, email TEXT,
          name TEXT, picture TEXT, created_at TEXT);
        INSERT INTO lecture (id,title,filename,status,panopto_session_id)
          VALUES (1,'a','a.mp4','completed','s1');
        INSERT INTO panoptotoken VALUES (1,'tok','2026-01-01');
    """)
    c.commit()
    c.close()

    os.environ["DATABASE_URL"] = f"sqlite:///{path}"
    _reset_app_modules()
    from sqlalchemy import inspect
    from app.core.database import create_db_and_tables, engine

    create_db_and_tables()
    create_db_and_tables()  # idempotent

    insp = inspect(engine)
    lec = {col["name"] for col in insp.get_columns("lecture")}
    tok = {col["name"] for col in insp.get_columns("panoptotoken")}
    for col in ("ingest_attempts", "caption_attempts", "last_progress_at",
                "panopto_captions_synced_at", "panopto_sync_error"):
        assert col in lec, f"missing {col}"
    for col in ("access_token", "access_token_expires_at"):
        assert col in tok, f"missing {col}"
    assert "ix_lecture_panopto_session_id" in [i["name"] for i in insp.get_indexes("lecture")]

    c = sqlite3.connect(path)
    assert c.execute("SELECT count(*) FROM lecture").fetchone()[0] == 1, "data lost"
    c.close()


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def test_diagnostics_is_safe_and_authenticated():
    fresh_db(PANOPTO_SYNC_SECRET="s3cret")
    from fastapi.testclient import TestClient
    from app.core.database import engine
    from app.main import app
    import app.services.panopto_service as ps

    _seed_user(engine)
    client = TestClient(app)

    assert client.get("/panopto/diagnostics").status_code == 401
    assert client.get("/panopto/diagnostics", headers={"X-Sync-Secret": "wrong"}).status_code == 401
    assert client.post("/panopto/sync", headers={"X-Sync-Secret": "wrong"}).status_code == 401

    with patch.object(ps, "list_recent_sessions", return_value=[{"Id": "s1", "Name": "L"}]):
        r = client.get("/panopto/diagnostics", headers={"X-Sync-Secret": "s3cret"})
    assert r.status_code == 200
    body = r.text
    for secret in ("s3cret", "csec", "cid"):
        assert secret not in body, f"diagnostics leaked {secret!r}"

    # Panopto being unreachable is reported, not fatal.
    with patch.object(ps, "list_recent_sessions", side_effect=ps.PanoptoAPIError("down")):
        d = client.get("/panopto/diagnostics", headers={"X-Sync-Secret": "s3cret"}).json()
    assert d["panopto_folder"]["reachable"] is False

    # The removed debug endpoint must stay removed: it was unauthenticated and
    # ran a recursive filesystem glob on every request.
    assert client.get("/debug-env").status_code == 404


def test_pilot_controls_restricted_to_owner():
    """Anyone can create an account here with a Google login. Being signed in
    must not be enough to drive the pilot or read its Panopto folder."""
    fresh_db(PANOPTO_SYNC_SECRET="s3cret")
    from fastapi import HTTPException
    from sqlmodel import Session
    from app.core.database import engine
    from app.models import User
    import app.api.panopto_routes as pr

    with Session(engine) as db:
        db.add(User(google_sub="owner", email="owner@x.com"))
        db.add(User(google_sub="rando", email="rando@x.com"))
        db.commit()
        owner, rando = db.exec(__import__("sqlmodel").select(User).order_by(User.id)).all()

        assert pr._resolve_sync_owner(db, owner, "").id == owner.id

        try:
            pr._resolve_sync_owner(db, rando, "")
            raise AssertionError("a non-owner was allowed to drive the pilot")
        except HTTPException as e:
            assert e.status_code == 403

        # The poller still gets in with the secret, as the owner.
        assert pr._resolve_sync_owner(db, None, "s3cret").id == owner.id
        try:
            pr._resolve_sync_owner(db, None, "wrong")
            raise AssertionError("expected 401")
        except HTTPException as e:
            assert e.status_code == 401


def test_lectures_list_excludes_heavy_field():
    """words_json is the largest column and unused by the list screen, which
    polls every five seconds."""
    fresh_db()
    from app.main import app  # noqa: F401  (ensures routes are built)
    from app.api.routes import get_all_lectures

    excluded = getattr(get_all_lectures, "__route_exclude__", None)
    # FastAPI stores this on the route, so check the app's route table instead.
    from app.main import app as built
    route = next(r for r in built.routes if getattr(r, "path", "") == "/lectures")
    assert route.response_model_exclude == {"__all__": {"words_json"}}, route.response_model_exclude


def main():
    print("Panopto pipeline regression suite\n")
    sections = [
        ("OAuth token lifecycle", test_token_lifecycle),
        ("Refresh-token rotation is atomic", test_refresh_token_rotation_is_atomic),
        ("Stale access token recovers", test_stale_access_token_recovers),
        ("Claim prevents duplicate ingest", test_claim_prevents_duplicate_ingest),
        ("Backlog is rate limited", test_backlog_is_rate_limited),
        ("Stalled lecture reaped and retried", test_stalled_lecture_is_reaped_and_retried),
        ("Retries are bounded", test_retries_are_bounded),
        ("Failed download recorded and retryable", test_failed_download_is_recorded_and_retryable),
        ("Caption retry and reconcile", test_caption_push_retry_and_reconcile),
        ("Empty transcript recorded", test_empty_transcript_is_recorded_not_silent),
        ("Media cleanup", test_media_cleanup),
        ("Stale download sweep", test_stale_download_sweep),
        ("Transcription timeout and poll retry", test_transcription_timeout_and_poll_retry),
        ("Validation survives purifier failure", test_validation_survives_purifier_failure),
        ("Migration against old schema", test_migration_against_old_schema),
        ("Diagnostics safe and authenticated", test_diagnostics_is_safe_and_authenticated),
        ("Pilot controls restricted to owner", test_pilot_controls_restricted_to_owner),
        ("Lectures list excludes words_json", test_lectures_list_excludes_heavy_field),
    ]
    for name, fn in sections:
        check(name, fn)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for name, err in FAILED:
            print(f"  - {name}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
