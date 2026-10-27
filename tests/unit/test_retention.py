"""Data retention (plan Phase 7): opt-in sweep and the teacher's purge."""

from datetime import datetime, timedelta, timezone

import pytest

from app import db
from app.workshop import kb_client, retention, store
from conftest import launch  # noqa: E402


def work(user, link="link-1", guid="lms.test"):
    """A student who did a bit of everything."""
    scope = (user, link, guid)
    store.save_assistant(*scope, instructions="x", document_name="d.md", document_text="secret")
    t = store.start_turn(*scope, "Hola")
    store.finish_turn(t, "Bon dia", "m", store.LocalTrace().save(*scope, "chat", {}))
    store.save_reflection(*scope, {"q1": "x"}, submit=True)
    store.add_feedback(*scope, {}, {"status": "completed"}, "m", None)
    store.save_consent(*scope, "v1")
    return scope


def rows(scope):
    with db.get_conn() as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id = ? AND resource_link_id = ? "
                                "AND consumer_guid = ?", scope).fetchone()[0]
                for t in retention._STUDENT_TABLES}


@pytest.fixture
def fake_kb(monkeypatch):
    deleted = []
    monkeypatch.setattr(kb_client, "configured", lambda: True)
    monkeypatch.setattr(kb_client, "delete_collection", lambda cid: deleted.append(cid) or True)
    return deleted


@pytest.mark.parametrize("value, days", [("", None), ("0", None), ("abc", None), ("180", 180)])
def test_retention_is_opt_in(monkeypatch, value, days):
    monkeypatch.setenv("WORKSHOP_RETENTION_DAYS", value)
    assert retention.retention_days() == days


def test_without_the_setting_the_sweep_deletes_nothing(monkeypatch):
    monkeypatch.delenv("WORKSHOP_RETENTION_DAYS", raising=False)
    scope = work(db.upsert_user("u1", "lms.test", "u1", "A", ""))
    assert retention.sweep() == {"enabled": False}
    assert all(rows(scope).values())


def with_kb(scope, collection_id):
    store.add_student_collection(*scope, collection_id, {}, 1000, 200)
    return scope


def test_purge_student_removes_only_that_student_and_their_kb(fake_kb):
    a = with_kb(work(db.upsert_user("u1", "lms.test", "u1", "A", "")), 77)
    b = with_kb(work(db.upsert_user("u2", "lms.test", "u2", "B", "")), 78)
    retention.purge_student(*a)
    assert not any(rows(a).values()) and all(rows(b).values())
    assert fake_kb == [77] and store.student_collection(*a) is None
    assert store.student_collection(*b)["collection_id"] == 78


def test_purge_activity_removes_every_student_and_their_kbs(fake_kb):
    a = with_kb(work(db.upsert_user("u1", "lms.test", "u1", "A", "")), 77)
    b = with_kb(work(db.upsert_user("u2", "lms.test", "u2", "B", "")), 78)
    other = with_kb(work(db.upsert_user("u3", "lms.test", "u3", "C", ""), link="link-2"), 79)
    assert retention.purge_activity("link-1", "lms.test") == {"students": 2}
    assert not any(rows(a).values()) and not any(rows(b).values())
    assert all(rows(other).values()), "other activities are untouched"
    assert sorted(fake_kb) == [77, 78] and store.kb_trash() == []


def test_the_sweep_removes_only_what_has_been_idle_long_enough(monkeypatch, fake_kb):
    monkeypatch.setenv("WORKSHOP_RETENTION_DAYS", "30")
    old = with_kb(work(db.upsert_user("u1", "lms.test", "u1", "A", "")), 77)
    recent = with_kb(work(db.upsert_user("u2", "lms.test", "u2", "B", ""), link="link-2"), 78)
    with db.get_conn() as conn:  # age everything of link-1 by 40 days
        long_ago = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(timespec="seconds")
        for table, col in (("student_assistant", "updated_at"), ("turns", "created_at"),
                           ("reflections", "updated_at"), ("consent", "decided_at"),
                           ("kb_collections", "created_at")):
            conn.execute(f"UPDATE {table} SET {col} = ? WHERE resource_link_id = 'link-1'", (long_ago,))

    result = retention.sweep()
    assert result == {"enabled": True, "days": 30, "students": 1}
    assert not any(rows(old).values()) and all(rows(recent).values())
    assert fake_kb == [77], "the recent student's knowledge base stays"


def test_a_kb_deletion_that_fails_is_retried(monkeypatch, fake_kb):
    scope = with_kb(work(db.upsert_user("u1", "lms.test", "u1", "A", "")), 77)

    def down(cid):
        raise kb_client.KbError("down")
    monkeypatch.setattr(kb_client, "delete_collection", down)
    retention.purge_student(*scope)
    assert not any(rows(scope).values()) and store.kb_trash() == [77]
    monkeypatch.setattr(kb_client, "delete_collection", lambda cid: fake_kb.append(cid) or True)
    assert retention.empty_kb_trash() == 1 and fake_kb == [77] and store.kb_trash() == []


def test_only_teachers_purge(client, fake_kb):
    student, _ = launch()
    assert client.post("/dashboard/purge", headers=student).status_code == 403
    teacher, _ = launch("Teacher", "t1", instructor=True)
    work(db.upsert_user("u9", "lms.test", "u9", "A", ""))
    assert client.post("/dashboard/purge", headers=teacher).json() == {"students": 1}
