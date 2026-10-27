"""SQLite storage. Plain sqlite3 from the standard library — no ORM, so the
whole persistence story is readable in this one file.

Multi-LMS safety: every LMS-scoped table carries `consumer_guid`
(tool_consumer_instance_guid from the launch), so two Moodles can host the
same tool without their resource_link ids colliding."""

import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lti_user_id TEXT NOT NULL,
    consumer_guid TEXT NOT NULL DEFAULT '',
    username TEXT,
    full_name TEXT,
    email TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (lti_user_id, consumer_guid)
);

CREATE TABLE IF NOT EXISTS activities (
    resource_link_id TEXT NOT NULL,
    consumer_guid TEXT NOT NULL DEFAULT '',
    context_id TEXT,
    context_title TEXT,
    title TEXT NOT NULL,
    assistant_model TEXT NOT NULL,
    assistant_name TEXT,
    grading_enabled INTEGER NOT NULL DEFAULT 0,
    min_turns INTEGER NOT NULL DEFAULT 3,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (resource_link_id, consumer_guid)
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    resource_link_id TEXT NOT NULL,
    consumer_guid TEXT NOT NULL DEFAULT '',
    is_instructor INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lti_nonces (
    nonce TEXT PRIMARY KEY,
    seen_at INTEGER NOT NULL
);

-- Runtime configuration set on the /admin page (see settings_store.py).
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Admin console sessions (separate from LTI sessions).
CREATE TABLE IF NOT EXISTS admin_sessions (
    token TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

-- Grade-passback plumbing captured at launch time, per user per activity.
CREATE TABLE IF NOT EXISTS enrollments (
    user_id INTEGER NOT NULL REFERENCES users(id),
    resource_link_id TEXT NOT NULL,
    consumer_guid TEXT NOT NULL DEFAULT '',
    lis_result_sourcedid TEXT,
    lis_outcome_service_url TEXT,
    first_launch_at TEXT NOT NULL,
    last_launch_at TEXT NOT NULL,
    PRIMARY KEY (user_id, resource_link_id, consumer_guid)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    resource_link_id TEXT NOT NULL,
    consumer_guid TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_thread
    ON messages (user_id, resource_link_id, consumer_guid, id);

CREATE TABLE IF NOT EXISTS grades (
    user_id INTEGER NOT NULL REFERENCES users(id),
    resource_link_id TEXT NOT NULL,
    consumer_guid TEXT NOT NULL DEFAULT '',
    proposed_score REAL,
    score REAL,
    feedback TEXT,
    sent_to_lms INTEGER NOT NULL DEFAULT 0,
    sent_at TEXT,
    PRIMARY KEY (user_id, resource_link_id, consumer_guid)
);
"""

# Schema changes after the template's base schema, as numbered migrations.
# PRAGMA user_version records the last one applied; each runs once, inside a
# transaction, so pilot data survives upgrades of the tool. Never edit an
# applied migration: add the next number.
_MIGRATIONS = {
    1: """
-- AI Workshop: the student's assistant (wizard steps 1, 2 and 4). The
-- document is stored as the text extracted from the upload, not the file.
CREATE TABLE IF NOT EXISTS student_assistant (
    user_id INTEGER NOT NULL REFERENCES users(id),
    resource_link_id TEXT NOT NULL,
    consumer_guid TEXT NOT NULL DEFAULT '',
    instructions TEXT NOT NULL DEFAULT '',
    document_name TEXT,
    document_text TEXT,
    document_bytes INTEGER,
    retrieval_enabled INTEGER NOT NULL DEFAULT 0,
    tools TEXT NOT NULL DEFAULT '[]',
    current_step INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, resource_link_id, consumer_guid)
);

-- What the tool sent to LAMB and why: the assembler trace, as JSON. Kinds:
-- a chat turn, a step-3 retrieval probe, a formative-feedback run.
CREATE TABLE IF NOT EXISTS traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    resource_link_id TEXT NOT NULL,
    consumer_guid TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL CHECK (kind IN ('chat', 'probe', 'feedback')),
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- One exchange of the wizard chat. A failed call is kept as status 'error'
-- and never becomes history the model sees again.
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    resource_link_id TEXT NOT NULL,
    consumer_guid TEXT NOT NULL DEFAULT '',
    user_content TEXT NOT NULL,
    assistant_content TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending', 'ok', 'error')),
    error TEXT,
    rag_mode TEXT NOT NULL DEFAULT 'off' CHECK (rag_mode IN ('on', 'off')),
    model TEXT,
    trace_id INTEGER REFERENCES traces(id),
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_turns_thread
    ON turns (user_id, resource_link_id, consumer_guid, id);

-- Step 6: the student's answers to the reflection prompts (JSON).
CREATE TABLE IF NOT EXISTS reflections (
    user_id INTEGER NOT NULL REFERENCES users(id),
    resource_link_id TEXT NOT NULL,
    consumer_guid TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    submitted_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, resource_link_id, consumer_guid)
);

-- Formative feedback from the engine, with the rubric it used. A suggestion
-- for the student only: never written to the gradebook.
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    resource_link_id TEXT NOT NULL,
    consumer_guid TEXT NOT NULL DEFAULT '',
    rubric TEXT NOT NULL,
    content TEXT NOT NULL,
    model TEXT,
    trace_id INTEGER REFERENCES traces(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS consent (
    user_id INTEGER NOT NULL REFERENCES users(id),
    resource_link_id TEXT NOT NULL,
    consumer_guid TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    decided_at TEXT NOT NULL,
    PRIMARY KEY (user_id, resource_link_id, consumer_guid)
);

-- Per-activity workshop settings. The engine assistant is NOT here: it is a
-- deployment setting on /admin (plan T13).
CREATE TABLE IF NOT EXISTS activity_settings (
    resource_link_id TEXT NOT NULL,
    consumer_guid TEXT NOT NULL DEFAULT '',
    rubric TEXT NOT NULL DEFAULT '{}',
    steps_enabled TEXT NOT NULL DEFAULT '[1, 2, 3, 4, 5, 6]',
    teacher_first INTEGER NOT NULL DEFAULT 0,
    consent_model_note TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (resource_link_id, consumer_guid)
);
""",
    2: """
-- Collections in the tool's own kb-server (plan 8C, T10). The collection id
-- is only ever read from here, never taken from a request (T9). One course
-- collection per activity (user_id NULL); student collections would carry
-- the user. The embedding model is fixed when a collection is created.
CREATE TABLE IF NOT EXISTS kb_collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    consumer_guid TEXT NOT NULL DEFAULT '',
    resource_link_id TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('course', 'student')),
    user_id INTEGER REFERENCES users(id),
    collection_id INTEGER NOT NULL,
    embedding TEXT NOT NULL DEFAULT '{}',
    chunk_size INTEGER NOT NULL,
    chunk_overlap INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
-- UNIQUE would let several NULL user_ids through: index each scope apart.
CREATE UNIQUE INDEX IF NOT EXISTS idx_kb_course
    ON kb_collections (consumer_guid, resource_link_id) WHERE scope = 'course';
CREATE UNIQUE INDEX IF NOT EXISTS idx_kb_student
    ON kb_collections (consumer_guid, resource_link_id, user_id) WHERE scope = 'student';
""",
    3: """
-- "New conversation": each turn records the conversation it belongs to, and
-- only the current conversation is sent as history. Earlier turns stay stored
-- (and visible to the teacher). Without it the A/B comparison is not clean:
-- asking the same question again with the knowledge base off, the model can
-- read its earlier answer in the history.
ALTER TABLE student_assistant ADD COLUMN conversation INTEGER NOT NULL DEFAULT 0;
ALTER TABLE turns ADD COLUMN conversation INTEGER NOT NULL DEFAULT 0;
""",
    4: """
-- Step 3 searches the student's own step-2 document (plan T15), so there is
-- no course knowledge base any more. kb_trash holds the kb-server collections
-- still to delete: a replaced or removed document, a purged student, and the
-- course collections of migration 2. A row goes once the kb-server confirms,
-- so a removal made while it was down still happens (retention.empty_kb_trash).
CREATE TABLE IF NOT EXISTS kb_trash (
    collection_id INTEGER PRIMARY KEY,
    retired_at TEXT NOT NULL
);
INSERT OR IGNORE INTO kb_trash (collection_id, retired_at)
    SELECT collection_id, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')
    FROM kb_collections WHERE scope = 'course';
DELETE FROM kb_collections WHERE scope = 'course';
""",
    5: """
-- Tool rounds are history too (plan T16): later turns resend each round as
-- the model wrote the call and as the result went back, then the model's last
-- reply. assistant_content stays what the student saw (the text written
-- before each call, then that last reply). Both NULL for a turn without
-- tool calls, and for turns stored before this migration.
ALTER TABLE turns ADD COLUMN tool_rounds TEXT;
ALTER TABLE turns ADD COLUMN last_reply TEXT;
""",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def get_conn():
    conn = sqlite3.connect(config.TOOL_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    os.makedirs(os.path.dirname(config.TOOL_DB_PATH) or ".", exist_ok=True)
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_SCHEMA)
        migrate(conn)


def migrate(conn, migrations: dict[int, str] = _MIGRATIONS) -> int:
    """Apply the migrations newer than PRAGMA user_version; return the
    resulting version."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version in sorted(v for v in migrations if v > current):
        # executescript commits first and adds no transaction of its own, so
        # the migration and its version bump commit together or not at all.
        try:
            conn.executescript(
                f"BEGIN;\n{migrations[version]}\nPRAGMA user_version = {int(version)};\nCOMMIT;")
        except Exception:
            conn.rollback()
            raise
        current = version
    return current


# --- users -------------------------------------------------------------------

def upsert_user(lti_user_id: str, consumer_guid: str, username: str,
                full_name: str, email: str) -> int:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO users (lti_user_id, consumer_guid, username, full_name, email, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (lti_user_id, consumer_guid) DO UPDATE SET
                 username = excluded.username,
                 full_name = excluded.full_name,
                 email = excluded.email""",
            (lti_user_id, consumer_guid, username, full_name, email, now_iso()),
        )
        row = conn.execute(
            "SELECT id FROM users WHERE lti_user_id = ? AND consumer_guid = ?",
            (lti_user_id, consumer_guid),
        ).fetchone()
        return row["id"]


# --- nonces (OAuth replay protection) ---------------------------------------

def check_and_store_nonce(nonce: str) -> bool:
    """True if the nonce is fresh; False if it was already used (replay)."""
    now = int(time.time())
    with get_conn() as conn:
        conn.execute("DELETE FROM lti_nonces WHERE seen_at < ?",
                     (now - config.LTI_NONCE_TTL_SECONDS,))
        try:
            conn.execute("INSERT INTO lti_nonces (nonce, seen_at) VALUES (?, ?)",
                         (nonce, now))
            return True
        except sqlite3.IntegrityError:
            return False


# --- admin sessions -----------------------------------------------------------

def create_admin_session(token: str, expires_at: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO admin_sessions (token, created_at, expires_at) VALUES (?, ?, ?)",
            (token, now_iso(), expires_at),
        )


def get_admin_session(token: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM admin_sessions WHERE token = ?", (token,)
        ).fetchone()


def delete_admin_session(token: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM admin_sessions WHERE token = ?", (token,))


# --- activities ---------------------------------------------------------------

def get_activity(resource_link_id: str, consumer_guid: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM activities WHERE resource_link_id = ? AND consumer_guid = ?",
            (resource_link_id, consumer_guid),
        ).fetchone()


def save_activity(resource_link_id: str, consumer_guid: str, context_id: str,
                  context_title: str, title: str, assistant_model: str,
                  assistant_name: str, grading_enabled: bool, min_turns: int,
                  created_by: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO activities
                 (resource_link_id, consumer_guid, context_id, context_title, title,
                  assistant_model, assistant_name, grading_enabled, min_turns,
                  created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (resource_link_id, consumer_guid) DO UPDATE SET
                 title = excluded.title,
                 assistant_model = excluded.assistant_model,
                 assistant_name = excluded.assistant_name,
                 grading_enabled = excluded.grading_enabled,
                 min_turns = excluded.min_turns""",
            (resource_link_id, consumer_guid, context_id, context_title, title,
             assistant_model, assistant_name, int(grading_enabled), min_turns,
             created_by, now_iso()),
        )


# --- enrollments (grade plumbing) ---------------------------------------------

def upsert_enrollment(user_id: int, resource_link_id: str, consumer_guid: str,
                      lis_result_sourcedid: str, lis_outcome_service_url: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO enrollments
                 (user_id, resource_link_id, consumer_guid,
                  lis_result_sourcedid, lis_outcome_service_url,
                  first_launch_at, last_launch_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (user_id, resource_link_id, consumer_guid) DO UPDATE SET
                 lis_result_sourcedid = COALESCE(NULLIF(excluded.lis_result_sourcedid, ''),
                                                 enrollments.lis_result_sourcedid),
                 lis_outcome_service_url = COALESCE(NULLIF(excluded.lis_outcome_service_url, ''),
                                                    enrollments.lis_outcome_service_url),
                 last_launch_at = excluded.last_launch_at""",
            (user_id, resource_link_id, consumer_guid,
             lis_result_sourcedid, lis_outcome_service_url, now_iso(), now_iso()),
        )


# --- messages -----------------------------------------------------------------

def add_message(user_id: int, resource_link_id: str, consumer_guid: str,
                role: str, content: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO messages (user_id, resource_link_id, consumer_guid, role, content, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, resource_link_id, consumer_guid, role, content, now_iso()),
        )


def get_messages(user_id: int, resource_link_id: str, consumer_guid: str, limit: int = 200):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT role, content, created_at FROM messages
               WHERE user_id = ? AND resource_link_id = ? AND consumer_guid = ?
               ORDER BY id DESC LIMIT ?""",
            (user_id, resource_link_id, consumer_guid, limit),
        ).fetchall()
        return list(reversed(rows))


# --- grades + participant roster ------------------------------------------------

def participants(resource_link_id: str, consumer_guid: str):
    """Roster for the instructor dashboard: everyone who launched as a
    student (teachers are not enrolled, so a teacher's own trial run is not
    listed), their answered workshop messages, submission, latest formative
    feedback, and their grade row if any."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT u.id AS user_id, u.full_name, u.email,
                      e.lis_result_sourcedid,
                      (SELECT COUNT(*) FROM turns t
                        WHERE t.user_id = u.id
                          AND t.resource_link_id = e.resource_link_id
                          AND t.consumer_guid = e.consumer_guid
                          AND t.status = 'ok') AS user_turns,
                      (SELECT r.submitted_at FROM reflections r
                        WHERE r.user_id = u.id
                          AND r.resource_link_id = e.resource_link_id
                          AND r.consumer_guid = e.consumer_guid) AS submitted_at,
                      (SELECT f.content FROM feedback f
                        WHERE f.user_id = u.id
                          AND f.resource_link_id = e.resource_link_id
                          AND f.consumer_guid = e.consumer_guid
                        ORDER BY f.id DESC LIMIT 1) AS feedback_json,
                      g.proposed_score, g.score, g.feedback, g.sent_to_lms, g.sent_at
               FROM enrollments e
               JOIN users u ON u.id = e.user_id
               LEFT JOIN grades g ON g.user_id = e.user_id
                 AND g.resource_link_id = e.resource_link_id
                 AND g.consumer_guid = e.consumer_guid
               WHERE e.resource_link_id = ? AND e.consumer_guid = ?
               ORDER BY u.full_name""",
            (resource_link_id, consumer_guid),
        ).fetchall()


def set_grade(user_id: int, resource_link_id: str, consumer_guid: str,
              score: float, feedback: str, proposed_score=None) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO grades (user_id, resource_link_id, consumer_guid,
                                   proposed_score, score, feedback, sent_to_lms)
               VALUES (?, ?, ?, ?, ?, ?, 0)
               ON CONFLICT (user_id, resource_link_id, consumer_guid) DO UPDATE SET
                 proposed_score = COALESCE(excluded.proposed_score, grades.proposed_score),
                 score = excluded.score,
                 feedback = excluded.feedback,
                 sent_to_lms = 0""",
            (user_id, resource_link_id, consumer_guid, proposed_score, score, feedback),
        )


def mark_grade_sent(user_id: int, resource_link_id: str, consumer_guid: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE grades SET sent_to_lms = 1, sent_at = ?
               WHERE user_id = ? AND resource_link_id = ? AND consumer_guid = ?""",
            (now_iso(), user_id, resource_link_id, consumer_guid),
        )


def gradable_rows(resource_link_id: str, consumer_guid: str):
    """Grades with a final score, not yet sent, whose enrollment has passback
    plumbing (sourcedid + outcome URL)."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT g.user_id, g.score, g.feedback,
                      e.lis_result_sourcedid, e.lis_outcome_service_url
               FROM grades g
               JOIN enrollments e ON e.user_id = g.user_id
                 AND e.resource_link_id = g.resource_link_id
                 AND e.consumer_guid = g.consumer_guid
               WHERE g.resource_link_id = ? AND g.consumer_guid = ?
                 AND g.score IS NOT NULL AND g.sent_to_lms = 0
                 AND e.lis_result_sourcedid IS NOT NULL
                 AND e.lis_outcome_service_url IS NOT NULL""",
            (resource_link_id, consumer_guid),
        ).fetchall()
