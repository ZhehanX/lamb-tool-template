"""Data retention (plan Phase 7, the old branch's decision C9).

Nothing is deleted automatically unless the deployment opts in with
WORKSHOP_RETENTION_DAYS (the old branch recommended 180). Then a sweep — once
at startup and once a day — removes:

- a student's workshop data (their assistant and document text, the
  conversation and its traces, reflection, feedback, consent, and their
  document's collection in the tool's kb-server: vectors, stored file,
  records) once they have not used the activity for that many days.

A teacher can also delete all student data of an activity at any time from
the dashboard (purge_activity). Grades and LTI enrollment rows belong to the
template's grade passback and are left alone.

A collection is deleted in the kb-server through kb_trash: if the kb-server
cannot be reached, it stays there and empty_kb_trash retries it at startup
and once a day, retention setting or not, since the deletion was already
decided."""

import logging
import os
from datetime import datetime, timedelta, timezone

from ..db import get_conn
from . import kb_client, store

logger = logging.getLogger("workshop.retention")

RETENTION_DAYS_ENV = "WORKSHOP_RETENTION_DAYS"
_STUDENT_TABLES = ("turns", "traces", "reflections", "feedback", "consent", "student_assistant")


def retention_days() -> int | None:
    value = os.getenv(RETENTION_DAYS_ENV, "").strip()
    try:
        days = int(value)
    except ValueError:
        return None
    return days if days > 0 else None


def _delete_trashed(collection_id: int) -> bool:
    """Delete one kb_trash collection in the kb-server; False if that cannot
    be done now (it stays in kb_trash)."""
    if not kb_client.configured():
        return False
    try:
        kb_client.delete_collection(collection_id)  # False when already gone
    except kb_client.KbError as e:
        logger.warning("kb collection %s not deleted yet: %s", collection_id, e)
        return False
    store.forget_trashed(collection_id)
    return True


def drop_student_kb(user_id: int, resource_link_id: str, consumer_guid: str) -> bool:
    """Stop searching the student's document and delete its collection
    (now, or later through kb_trash). False if they had none."""
    collection_id = store.retire_student_collection(user_id, resource_link_id, consumer_guid)
    if collection_id is None:
        return False
    _delete_trashed(collection_id)
    return True


def empty_kb_trash() -> int:
    """Retry the deletions the kb-server has not confirmed; return how many
    went through."""
    return sum(_delete_trashed(cid) for cid in store.kb_trash())


def purge_student(user_id: int, resource_link_id: str, consumer_guid: str) -> None:
    drop_student_kb(user_id, resource_link_id, consumer_guid)
    with get_conn() as conn:
        for table in _STUDENT_TABLES:
            conn.execute(
                f"DELETE FROM {table} WHERE user_id = ? AND resource_link_id = ? AND consumer_guid = ?",
                (user_id, resource_link_id, consumer_guid))


def purge_activity(resource_link_id: str, consumer_guid: str) -> dict:
    """Everything the workshop stored for an activity's students (the
    teacher's "delete all student data")."""
    with get_conn() as conn:
        users = [r[0] for r in conn.execute(
            """SELECT DISTINCT user_id FROM student_assistant WHERE resource_link_id = ? AND consumer_guid = ?
               UNION SELECT DISTINCT user_id FROM turns WHERE resource_link_id = ? AND consumer_guid = ?
               UNION SELECT DISTINCT user_id FROM reflections WHERE resource_link_id = ? AND consumer_guid = ?""",
            (resource_link_id, consumer_guid) * 3)]
    for uid in users:
        purge_student(uid, resource_link_id, consumer_guid)
    return {"students": len(users)}


def sweep(now: datetime | None = None) -> dict:
    """The opt-in automatic deletion. Returns what was removed."""
    days = retention_days()
    if days is None:
        return {"enabled": False}
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat(timespec="seconds")
    with get_conn() as conn:
        # Last use per student and activity: the latest of their design edits,
        # turns, reflection and consent.
        stale = conn.execute(
            """SELECT user_id, resource_link_id, consumer_guid, MAX(at) AS last FROM (
                 SELECT user_id, resource_link_id, consumer_guid, updated_at AS at FROM student_assistant
                 UNION ALL SELECT user_id, resource_link_id, consumer_guid, created_at FROM turns
                 UNION ALL SELECT user_id, resource_link_id, consumer_guid, updated_at FROM reflections
                 UNION ALL SELECT user_id, resource_link_id, consumer_guid, decided_at FROM consent)
               GROUP BY user_id, resource_link_id, consumer_guid HAVING last < ?""",
            (cutoff,)).fetchall()
    for r in stale:
        purge_student(r["user_id"], r["resource_link_id"], r["consumer_guid"])
    result = {"enabled": True, "days": days, "students": len(stale)}
    if stale:
        logger.info("retention sweep: %s", result)
    return result
