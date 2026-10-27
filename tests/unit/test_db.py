"""Schema migrations (PRAGMA user_version) and the workshop store."""

import sqlite3

import pytest

from app import db
from app.workshop import store

WORKSHOP_TABLES = {"student_assistant", "traces", "turns", "reflections", "feedback",
                   "consent", "activity_settings"}


def tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_fresh_database_is_at_the_latest_version():
    with db.get_conn() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == max(db._MIGRATIONS)
        assert WORKSHOP_TABLES <= tables(conn)
        assert "messages" in tables(conn), "the template's tables are still there"


def test_init_db_is_idempotent_and_keeps_data():
    uid = db.upsert_user("u1", "lms.test", "u1", "Alumna", "a@lms.test")
    store.save_assistant(uid, "link-1", "lms.test", instructions="Sigues breu.")
    db.init_db()
    db.init_db()
    assert store.get_assistant(uid, "link-1", "lms.test")["instructions"] == "Sigues breu."


def test_a_failing_migration_changes_nothing(tmp_path):
    conn = sqlite3.connect(tmp_path / "m.db")
    assert db.migrate(conn, {1: "CREATE TABLE a (x INTEGER);"}) == 1
    broken = {2: "CREATE TABLE b (x INTEGER);\nCREATE TABLE a (x INTEGER);"}  # a exists
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(conn, {1: "unused", **broken})
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert "b" not in tables(conn), "the half-applied migration was rolled back"
    assert db.migrate(conn, {1: "unused", 2: "CREATE TABLE b (x INTEGER);"}) == 2
    conn.close()


def test_migration_4_moves_the_course_collections_to_the_trash(tmp_path):
    conn = sqlite3.connect(tmp_path / "m.db")
    conn.executescript(db._SCHEMA)
    db.migrate(conn, {v: sql for v, sql in db._MIGRATIONS.items() if v < 4})
    conn.executescript(
        """INSERT INTO users (lti_user_id, created_at) VALUES ('u1', 'now');
           INSERT INTO kb_collections (resource_link_id, scope, user_id, collection_id,
                                       chunk_size, chunk_overlap, created_at)
           VALUES ('link-1', 'course', NULL, 9, 1000, 200, 'then'),
                  ('link-1', 'student', 1, 12, 1000, 200, 'then');""")
    assert db.migrate(conn, {v: sql for v, sql in db._MIGRATIONS.items() if v <= 4}) == 4
    assert [r[0] for r in conn.execute("SELECT collection_id FROM kb_trash")] == [9]
    assert [r[0] for r in conn.execute("SELECT collection_id FROM kb_collections")] == [12]
    conn.close()


def test_assistant_upsert_updates_only_given_fields():
    uid = db.upsert_user("u1", "lms.test", "u1", "Alumna", "a@lms.test")
    store.save_assistant(uid, "link-1", "lms.test", instructions="Una", tools=["calculator"])
    store.save_assistant(uid, "link-1", "lms.test", document_name="d.md", document_text="Text")
    row = store.get_assistant(uid, "link-1", "lms.test")
    assert (row["instructions"], row["tools"], row["document_name"]) == (
        "Una", '["calculator"]', "d.md")
    with pytest.raises(ValueError):
        store.save_assistant(uid, "link-1", "lms.test", owner="x")


def test_rows_are_scoped_by_consumer_guid():
    uid = db.upsert_user("u1", "lms-a", "u1", "Alumna", "a@a")
    store.save_assistant(uid, "link-1", "lms-a", instructions="A")
    assert store.get_assistant(uid, "link-1", "lms-b") is None


def test_history_skips_failed_turns():
    uid = db.upsert_user("u1", "lms.test", "u1", "Alumna", "a@lms.test")
    traces = store.LocalTrace()
    t1 = store.start_turn(uid, "link-1", "lms.test", "Hola")
    store.finish_turn(t1, "Bon dia!", "qwen3.5:9b",
                      traces.save(uid, "link-1", "lms.test", "chat", {"messages": []}))
    t2 = store.start_turn(uid, "link-1", "lms.test", "Text llarguíssim")
    store.fail_turn(t2, "model_error", None)
    store.start_turn(uid, "link-1", "lms.test", "Encara esperant")

    assert store.history(uid, "link-1", "lms.test") == [
        {"role": "user", "content": "Hola"}, {"role": "assistant", "content": "Bon dia!"}]
    statuses = [t["status"] for t in store.get_turns(uid, "link-1", "lms.test")]
    assert statuses == ["ok", "error", "pending"]


def test_history_carries_the_tool_rounds():
    """T16; a turn stored before migration 5 has none and keeps its old shape."""
    uid = db.upsert_user("u1", "lms.test", "u1", "Alumna", "a@lms.test")
    old = store.start_turn(uid, "link-1", "lms.test", "Hola")
    store.finish_turn(old, "Bon dia!", None, None)
    t = store.start_turn(uid, "link-1", "lms.test", "Quant fa 17*23?")
    call, result = 'Ho calculo.\n<tool name="calculator">17*23</tool>', \
        '<tool_result name="calculator">391</tool_result>'
    store.finish_turn(t, "Ho calculo.\nFa 391.", None, None,
                      [{"name": "calculator", "call": call, "result": result}], last_reply="Fa 391.")

    assert store.history(uid, "link-1", "lms.test") == [
        {"role": "user", "content": "Hola"}, {"role": "assistant", "content": "Bon dia!"},
        {"role": "user", "content": "Quant fa 17*23?"},
        {"role": "assistant", "content": call, "tool_round": "call", "tool": "calculator"},
        {"role": "user", "content": result, "tool_round": "result", "tool": "calculator"},
        {"role": "assistant", "content": "Fa 391."}]
    with db.get_conn() as conn:
        row = conn.execute("SELECT tool_rounds, last_reply FROM turns WHERE id = ?", (old,)).fetchone()
    assert tuple(row) == (None, None)


def test_local_trace_round_trip():
    uid = db.upsert_user("u1", "lms.test", "u1", "Alumna", "a@lms.test")
    data = {"messages": [{"role": "user", "content": "Què és l'aigua? 🌱"}], "tokens_estimated": 9}
    tid = store.LocalTrace().save(uid, "link-1", "lms.test", "probe", data)
    assert store.LocalTrace().get(tid) == data
    assert store.LocalTrace().get(tid + 1) is None
    with pytest.raises(sqlite3.IntegrityError):
        store.LocalTrace().save(uid, "link-1", "lms.test", "other", data)
