"""Workshop data access on top of app/db.py (tables: migration 1 there).

Every row is scoped by (user_id, resource_link_id, consumer_guid), the
template's multi-LMS convention."""

import json

from ..db import get_conn, now_iso

_ASSISTANT_FIELDS = ("instructions", "document_name", "document_text", "document_bytes",
                     "retrieval_enabled", "tools", "current_step", "conversation")


# --- the student's assistant ---------------------------------------------------

def get_assistant(user_id: int, resource_link_id: str, consumer_guid: str):
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM student_assistant
               WHERE user_id = ? AND resource_link_id = ? AND consumer_guid = ?""",
            (user_id, resource_link_id, consumer_guid),
        ).fetchone()


def save_assistant(user_id: int, resource_link_id: str, consumer_guid: str, **fields) -> None:
    """Upsert the given fields; the others keep their stored (or default) value."""
    unknown = set(fields) - set(_ASSISTANT_FIELDS)
    if unknown:
        raise ValueError(f"unknown student_assistant fields: {sorted(unknown)}")
    if "tools" in fields and not isinstance(fields["tools"], str):
        fields["tools"] = json.dumps(fields["tools"])
    cols = list(fields)
    with get_conn() as conn:
        conn.execute(
            f"""INSERT INTO student_assistant
                  (user_id, resource_link_id, consumer_guid, {", ".join(cols + ["updated_at"])})
                VALUES (?, ?, ?, {", ".join("?" for _ in cols + ["updated_at"])})
                ON CONFLICT (user_id, resource_link_id, consumer_guid) DO UPDATE SET
                  {", ".join(f"{c} = excluded.{c}" for c in cols + ["updated_at"])}""",
            (user_id, resource_link_id, consumer_guid, *fields.values(), now_iso()),
        )


# --- chat turns ----------------------------------------------------------------

def start_turn(user_id: int, resource_link_id: str, consumer_guid: str,
               user_content: str, rag_mode: str = "off") -> int:
    current = conversation(user_id, resource_link_id, consumer_guid)
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO turns (user_id, resource_link_id, consumer_guid, user_content,
                                  status, rag_mode, conversation, created_at)
               VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (user_id, resource_link_id, consumer_guid, user_content, rag_mode, current, now_iso()),
        )
        return cur.lastrowid


def finish_turn(turn_id: int, assistant_content: str, model: str | None,
                trace_id: int | None, tool_rounds: list[dict] = (),
                last_reply: str | None = None) -> None:
    """tool_rounds: [{name, call, result}], the messages each round added to
    the request (T16); last_reply: what the model wrote after them."""
    rounds = json.dumps(list(tool_rounds), ensure_ascii=False) if tool_rounds else None
    with get_conn() as conn:
        conn.execute(
            """UPDATE turns SET assistant_content = ?, status = 'ok', model = ?,
                                trace_id = ?, tool_rounds = ?, last_reply = ?, completed_at = ?
               WHERE id = ?""",
            (assistant_content, model, trace_id, rounds, last_reply if rounds else None,
             now_iso(), turn_id),
        )


def fail_turn(turn_id: int, error: str, trace_id: int | None) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE turns SET status = 'error', error = ?, trace_id = ?, completed_at = ?
               WHERE id = ?""",
            (error, trace_id, now_iso(), turn_id),
        )


def get_turns(user_id: int, resource_link_id: str, consumer_guid: str, limit: int = 200):
    """Turns, oldest first, each with `retrieved`: how many course excerpts
    went into its prompt (read from its trace), for the A/B comparison."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT t.*, COALESCE((
                   SELECT COUNT(*) FROM traces tr,
                          json_each(tr.data, '$.retrieval.candidates') AS c
                   WHERE tr.id = t.trace_id AND json_extract(c.value, '$.selected') = 1
               ), 0) AS retrieved
               FROM turns t
               WHERE t.user_id = ? AND t.resource_link_id = ? AND t.consumer_guid = ?
               ORDER BY t.id DESC LIMIT ?""",
            (user_id, resource_link_id, consumer_guid, limit),
        ).fetchall()
        return list(reversed(rows))


def conversation(user_id: int, resource_link_id: str, consumer_guid: str) -> int:
    """The number of the current conversation (0 for the first)."""
    row = get_assistant(user_id, resource_link_id, consumer_guid)
    return row["conversation"] if row is not None else 0


def new_conversation(user_id: int, resource_link_id: str, consumer_guid: str) -> int:
    """Start a new conversation: the turns so far stop being history."""
    current = conversation(user_id, resource_link_id, consumer_guid) + 1
    save_assistant(user_id, resource_link_id, consumer_guid, conversation=current)
    return current


def history(user_id: int, resource_link_id: str, consumer_guid: str,
            limit: int = 200) -> list[dict]:
    """Earlier exchanges of the current conversation as {role, content}
    messages for the assembler: the student's message, the turn's tool rounds
    as they were sent (T16; tagged `tool_round` "call" or "result", with the
    `tool` name), then the reply. Only completed turns: a failed call is not
    something the model said."""
    current = conversation(user_id, resource_link_id, consumer_guid)
    messages = []
    for t in get_turns(user_id, resource_link_id, consumer_guid, limit):
        if t["status"] != "ok" or t["conversation"] != current:
            continue
        messages.append({"role": "user", "content": t["user_content"]})
        rounds = json.loads(t["tool_rounds"]) if t["tool_rounds"] else []
        for r in rounds:
            messages.append({"role": "assistant", "content": r["call"],
                             "tool_round": "call", "tool": r["name"]})
            messages.append({"role": "user", "content": r["result"],
                             "tool_round": "result", "tool": r["name"]})
        messages.append({"role": "assistant",
                         "content": t["last_reply"] if rounds else t["assistant_content"]})
    return messages


# --- traces --------------------------------------------------------------------

class LocalTrace:
    """TraceSource backed by the tool's SQLite (`traces` table)."""

    def save(self, user_id: int, resource_link_id: str, consumer_guid: str,
             kind: str, data: dict) -> int:
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO traces (user_id, resource_link_id, consumer_guid, kind, data, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, resource_link_id, consumer_guid, kind,
                 json.dumps(data, ensure_ascii=False), now_iso()),
            )
            return cur.lastrowid

    def get(self, trace_id: int) -> dict | None:
        with get_conn() as conn:
            row = conn.execute("SELECT data FROM traces WHERE id = ?", (trace_id,)).fetchone()
        return json.loads(row["data"]) if row else None


def get_trace_for(trace_id: int, user_id: int, resource_link_id: str, consumer_guid: str):
    """A trace, only if it belongs to this user in this activity."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT data FROM traces WHERE id = ? AND user_id = ?
                 AND resource_link_id = ? AND consumer_guid = ?""",
            (trace_id, user_id, resource_link_id, consumer_guid),
        ).fetchone()
    return json.loads(row["data"]) if row else None


# --- step 6: reflection ----------------------------------------------------------

def get_reflection(user_id: int, resource_link_id: str, consumer_guid: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT content, submitted_at, updated_at FROM reflections
               WHERE user_id = ? AND resource_link_id = ? AND consumer_guid = ?""",
            (user_id, resource_link_id, consumer_guid),
        ).fetchone()
    if row is None:
        return None
    return {"answers": json.loads(row["content"]), "submitted_at": row["submitted_at"],
            "updated_at": row["updated_at"]}


def save_reflection(user_id: int, resource_link_id: str, consumer_guid: str,
                    answers: dict, submit: bool = False) -> None:
    """Save the answers; `submit` also stamps the first submission time."""
    now = now_iso()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO reflections (user_id, resource_link_id, consumer_guid,
                                        content, submitted_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (user_id, resource_link_id, consumer_guid) DO UPDATE SET
                 content = excluded.content,
                 submitted_at = COALESCE(reflections.submitted_at, excluded.submitted_at),
                 updated_at = excluded.updated_at""",
            (user_id, resource_link_id, consumer_guid, json.dumps(answers, ensure_ascii=False),
             now if submit else None, now),
        )


# --- knowledge base collections (Phase 8) ------------------------------------------
# One per student and activity: their step-2 document, searched in step 3 (T15).

def student_collection(user_id: int, resource_link_id: str, consumer_guid: str):
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM kb_collections
               WHERE user_id = ? AND resource_link_id = ? AND consumer_guid = ?
                 AND scope = 'student'""",
            (user_id, resource_link_id, consumer_guid),
        ).fetchone()


def add_student_collection(user_id: int, resource_link_id: str, consumer_guid: str,
                           collection_id: int, embedding: dict, chunk_size: int,
                           chunk_overlap: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO kb_collections (consumer_guid, resource_link_id, scope, user_id,
                                           collection_id, embedding, chunk_size, chunk_overlap,
                                           created_at)
               VALUES (?, ?, 'student', ?, ?, ?, ?, ?, ?)""",
            (consumer_guid, resource_link_id, user_id, collection_id, json.dumps(embedding),
             chunk_size, chunk_overlap, now_iso()),
        )


def retire_student_collection(user_id: int, resource_link_id: str, consumer_guid: str) -> int | None:
    """Move the student's collection to kb_trash (migration 4) and return its
    id, or None if they have none. From here on nothing searches it."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT collection_id FROM kb_collections
               WHERE user_id = ? AND resource_link_id = ? AND consumer_guid = ?
                 AND scope = 'student'""",
            (user_id, resource_link_id, consumer_guid),
        ).fetchone()
        if row is None:
            return None
        conn.execute("INSERT OR IGNORE INTO kb_trash (collection_id, retired_at) VALUES (?, ?)",
                     (row["collection_id"], now_iso()))
        conn.execute(
            """DELETE FROM kb_collections
               WHERE user_id = ? AND resource_link_id = ? AND consumer_guid = ?
                 AND scope = 'student'""",
            (user_id, resource_link_id, consumer_guid),
        )
        return row["collection_id"]


def kb_trash() -> list[int]:
    with get_conn() as conn:
        return [r[0] for r in conn.execute("SELECT collection_id FROM kb_trash ORDER BY retired_at")]


def forget_trashed(collection_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM kb_trash WHERE collection_id = ?", (collection_id,))


# --- activity settings (teacher) -------------------------------------------------

_SETTINGS_FIELDS = ("rubric", "steps_enabled", "teacher_first", "consent_model_note")


def get_settings(resource_link_id: str, consumer_guid: str) -> dict:
    """The activity's workshop settings, with defaults when never saved."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM activity_settings WHERE resource_link_id = ? AND consumer_guid = ?",
            (resource_link_id, consumer_guid),
        ).fetchone()
    if row is None:
        return {"rubric": {}, "steps_enabled": [1, 2, 3, 4, 5, 6], "teacher_first": False,
                "consent_model_note": "", "updated_at": None}
    return {"rubric": json.loads(row["rubric"] or "{}"),
            "steps_enabled": json.loads(row["steps_enabled"]),
            "teacher_first": bool(row["teacher_first"]),
            "consent_model_note": row["consent_model_note"], "updated_at": row["updated_at"]}


def save_settings(resource_link_id: str, consumer_guid: str, **fields) -> None:
    unknown = set(fields) - set(_SETTINGS_FIELDS)
    if unknown:
        raise ValueError(f"unknown activity_settings fields: {sorted(unknown)}")
    for key in ("rubric", "steps_enabled"):
        if key in fields and not isinstance(fields[key], str):
            fields[key] = json.dumps(fields[key], ensure_ascii=False)
    if "teacher_first" in fields:
        fields["teacher_first"] = int(bool(fields["teacher_first"]))
    cols = list(fields) + ["updated_at"]
    with get_conn() as conn:
        conn.execute(
            f"""INSERT INTO activity_settings (resource_link_id, consumer_guid, {", ".join(cols)})
                VALUES (?, ?, {", ".join("?" for _ in cols)})
                ON CONFLICT (resource_link_id, consumer_guid) DO UPDATE SET
                  {", ".join(f"{c} = excluded.{c}" for c in cols)}""",
            (resource_link_id, consumer_guid, *fields.values(), now_iso()),
        )


# --- step 6: formative feedback -----------------------------------------------------

def add_feedback(user_id: int, resource_link_id: str, consumer_guid: str, rubric: dict,
                 content: dict, model: str | None, trace_id: int | None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO feedback (user_id, resource_link_id, consumer_guid, rubric, content,
                                     model, trace_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, resource_link_id, consumer_guid, json.dumps(rubric, ensure_ascii=False),
             json.dumps(content, ensure_ascii=False), model, trace_id, now_iso()),
        )
        return cur.lastrowid


def latest_feedback(user_id: int, resource_link_id: str, consumer_guid: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT content, model, created_at FROM feedback
               WHERE user_id = ? AND resource_link_id = ? AND consumer_guid = ?
               ORDER BY id DESC LIMIT 1""",
            (user_id, resource_link_id, consumer_guid),
        ).fetchone()
    if row is None:
        return None
    return {**json.loads(row["content"]), "model": row["model"], "created_at": row["created_at"]}


def teacher_has_tried(resource_link_id: str, consumer_guid: str) -> bool:
    """Whether someone who launched this activity as an instructor has
    submitted their own run of the workshop ("teacher first")."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT EXISTS (
                 SELECT 1 FROM reflections r
                 JOIN sessions s ON s.user_id = r.user_id
                   AND s.resource_link_id = r.resource_link_id
                   AND s.consumer_guid = r.consumer_guid AND s.is_instructor = 1
                 WHERE r.resource_link_id = ? AND r.consumer_guid = ?
                   AND r.submitted_at IS NOT NULL)""",
            (resource_link_id, consumer_guid),
        ).fetchone()[0] == 1


# --- consent -------------------------------------------------------------------------

def get_consent(user_id: int, resource_link_id: str, consumer_guid: str):
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM consent
               WHERE user_id = ? AND resource_link_id = ? AND consumer_guid = ?""",
            (user_id, resource_link_id, consumer_guid),
        ).fetchone()


def save_consent(user_id: int, resource_link_id: str, consumer_guid: str, version: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO consent (user_id, resource_link_id, consumer_guid, version, accepted, decided_at)
               VALUES (?, ?, ?, ?, 1, ?)
               ON CONFLICT (user_id, resource_link_id, consumer_guid) DO UPDATE SET
                 version = excluded.version, accepted = 1, decided_at = excluded.decided_at""",
            (user_id, resource_link_id, consumer_guid, version, now_iso()),
        )


def turns_since(user_id: int, resource_link_id: str, consumer_guid: str, since_iso: str) -> int:
    with get_conn() as conn:
        return conn.execute(
            """SELECT COUNT(*) FROM turns WHERE user_id = ? AND resource_link_id = ?
                 AND consumer_guid = ? AND created_at >= ?""",
            (user_id, resource_link_id, consumer_guid, since_iso),
        ).fetchone()[0]
