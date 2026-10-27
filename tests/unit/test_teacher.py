"""The teacher side (plan Phase 7): setup, "teacher first", the dashboard."""

import json

from app import db, settings_store
from app.workshop import evaluation, store
from conftest import launch  # noqa: E402
from test_workshop_api import _signed_launch  # noqa: E402


def test_setup_shows_the_engine_and_the_default_rubric(client):
    settings_store.set_value("engine_model", "lamb_assistant.2")
    teacher, _ = launch("Teacher", "t1", instructor=True)
    page = client.get("/setup", headers=teacher).text
    assert "lamb_assistant.2" in page and "<select" not in page, "the engine is shown, not chosen"
    assert "Sources" in page and "Reflection" in page  # the default rubric, ready to edit


def test_setup_rejects_a_bad_rubric_with_a_reason(client):
    teacher, _ = launch("Teacher", "t1", instructor=True)
    r = client.post("/setup", headers=teacher, data={"title": "T", "rubric": "{not json"})
    assert r.status_code == 400 and "not valid JSON" in r.json()["error"]
    r = client.post("/setup", headers=teacher, data={"title": "T", "rubric": '{"criteria": []}'})
    assert r.status_code == 400 and "non-empty" in r.json()["error"]


def test_setup_saves_the_workshop_settings(client):
    settings_store.set_value("engine_model", "lamb_assistant.2")
    teacher, _ = launch("Teacher", "t1", instructor=True)
    rubric = {"criteria": [{"name": "Reflection", "levels": [{"label": "OK", "score": 1}]}]}
    r = client.post("/setup", headers=teacher, data={
        "title": "Taller", "rubric": json.dumps(rubric), "teacher_first": "on",
        "consent_model_note": " qwen3.5:9b al servidor de la universitat ", "min_turns": "2"})
    assert r.json()["ok"]
    saved = store.get_settings("link-1", "lms.test")
    assert saved["rubric"] == evaluation.validate_rubric(rubric)
    assert saved["teacher_first"] is True and saved["consent_model_note"] == "qwen3.5:9b al servidor de la universitat"
    assert db.get_activity("link-1", "lms.test")["assistant_model"] == "lamb_assistant.2"


def test_students_wait_until_the_teacher_has_tried_it(client):
    teacher, teacher_id = launch("Teacher", "t1", instructor=True)
    store.save_settings("link-1", "lms.test", teacher_first=True)

    def student_lands():
        r = client.post("/lti/launch", data=_signed_launch("Learner"), follow_redirects=False)
        return r.headers["location"].split("?")[0]

    assert student_lands() == "/waiting"
    # The teacher tries the workshop with their own session and submits.
    client.put("/workshop/api/reflection", headers=teacher, json={"answers": {"q1": "x"}, "submit": True})
    assert store.teacher_has_tried("link-1", "lms.test")
    assert student_lands() == "/workshop/"


def test_without_teacher_first_students_go_straight_in(client):
    launch("Teacher", "t1", instructor=True)
    r = client.post("/lti/launch", data=_signed_launch("Learner"), follow_redirects=False)
    assert r.headers["location"].startswith("/workshop/")


def test_the_dashboard_lists_workshop_work_but_not_the_teacher(client):
    teacher, teacher_id = launch("Teacher", "t1", instructor=True)
    client.post("/lti/launch", data=_signed_launch("Learner"), follow_redirects=False)
    student = db.upsert_user("u9", "lms.test", "u9", "Alumna", "")
    scope = (student, "link-1", "lms.test")
    t = store.start_turn(*scope, "Hola")
    store.finish_turn(t, "Bon dia", "qwen3.5:9b", None)
    store.save_reflection(*scope, {"q1": "x"}, submit=True)
    store.add_feedback(*scope, {}, {"status": "completed", "criteria": [], "total_score": 3.25,
                                    "max_score": 4}, "qwen3.5:9b", None)
    # The teacher's own trial run.
    store.start_turn(teacher_id, "link-1", "lms.test", "prova")

    page = client.get("/dashboard", headers=teacher).text
    assert "Alumna" in page and "3.25 / 4" in page
    assert "Teacher</a>" not in page, "the teacher's trial is not in the roster"
    assert client.get(f"/dashboard/student/{teacher_id}", headers=teacher).status_code == 404
    work = client.get(f"/dashboard/student/{student}", headers=teacher).text
    assert "Student [knowledge base OFF, excerpts sent: 0]: Hola" in work and "3.25" in work


def test_a_saved_grade_reads_back_on_the_0_to_10_scale(client):
    teacher, _ = launch("Teacher", "t1", instructor=True)
    client.post("/lti/launch", data=_signed_launch("Learner"), follow_redirects=False)
    student = db.upsert_user("u9", "lms.test", "u9", "Alumna", "")
    client.post("/grades/set", headers=teacher, json={"user_id": student, "score": 0.8, "feedback": ""})
    page = client.get("/dashboard", headers=teacher).text
    assert 'value="8"' in page and 'value="0.8"' not in page


def test_students_cannot_see_the_teacher_pages(client):
    student, _ = launch()
    assert client.get("/setup", headers=student).status_code == 403
    assert client.get("/dashboard/student/1", headers=student).status_code == 403
