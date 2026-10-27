"""Step 6: rubric-based formative feedback (plan Phase 7)."""

import json

import pytest

from app.workshop import evaluation as ev
from app.workshop import store
from conftest import launch  # noqa: E402

RUBRIC = ev.validate_rubric({
    "title": "Test",
    "criteria": [
        {"name": "Instructions", "weight": 1, "levels": [
            {"label": "Good", "score": 4}, {"label": "Weak", "score": 1}]},
        {"name": "Reflection", "weight": 3, "levels": [
            {"label": "Good", "score": 4}, {"label": "Weak", "score": 1}]},
    ]})


# --- rubric ------------------------------------------------------------------

def test_the_default_rubric_is_valid():
    rubric = ev.validate_rubric(ev.DEFAULT_RUBRIC)
    assert [c["name"] for c in rubric["criteria"]] == ["Instructions", "Sources", "Tools", "Reflection"]
    assert ev.max_score(rubric) == 4


@pytest.mark.parametrize("rubric, message", [
    ([], "JSON object"),
    ({"criteria": []}, "non-empty"),
    ({"criteria": [{"weight": 1}]}, "needs a \"name\""),
    ({"criteria": [{"name": "A", "levels": [{"label": "x", "score": 1}]},
                   {"name": "a", "levels": [{"label": "x", "score": 1}]}]}, "Two criteria"),
    ({"criteria": [{"name": "A", "weight": -1, "levels": [{"label": "x", "score": 1}]}]}, ">= 0"),
    ({"criteria": [{"name": "A", "levels": []}]}, "at least one level"),
    ({"criteria": [{"name": "A", "levels": [{"label": "x", "score": "high"}]}]}, "numeric"),
])
def test_rubric_errors_say_what_is_wrong(rubric, message):
    with pytest.raises(ev.RubricError) as e:
        ev.validate_rubric(rubric)
    assert message in str(e.value)


def test_the_instructions_list_every_criterion_and_level():
    text = ev.rubric_instructions(RUBRIC) + ev.build_evaluation_directive(RUBRIC)
    assert 'Criterion "Instructions" (weight 1)' in text and 'level "Weak" (score 1)' in text
    assert 'EXACTLY these 2 rubric criteria' in text


# --- reading the model's answer ----------------------------------------------------

def test_fenced_json_with_a_trailing_comma():
    data, note = ev.parse_evaluation_json('```json\n{"criteria": [{"criterion": "Instructions",},], }\n```')
    assert data["criteria"][0]["criterion"] == "Instructions" and note is None


def test_a_reply_cut_off_mid_string_is_repaired_and_flagged():
    data, note = ev.parse_evaluation_json('{"criteria": [{"criterion": "Reflection", "feedback": "Molt b')
    assert data["criteria"][0]["feedback"] == "Molt b…" and "cut off" in note


def test_unreadable_replies_raise():
    with pytest.raises(ValueError):
        ev.parse_evaluation_json("No puc avaluar-ho.")


def test_normalize_aligns_with_the_rubric_and_computes_the_total():
    data = {"criteria": [
        {"criterion": "Invented criterion", "score": 4, "feedback": "x"},
        {"criterion": "reflection (30%)", "level": "Good", "feedback": "Clar."},
        {"criterion": "Instructions", "level": "weak", "feedback": "Massa curtes."},
        {"criterion": "Instructions", "score": 4, "feedback": "repeated"},
    ], "overall_feedback": "Bona feina.", "total_score": 999}
    out = ev.normalize_evaluation(data, RUBRIC)
    assert [(c["criterion"], c["level"], c["score"]) for c in out["criteria"]] == [
        ("Instructions", "weak", 1.0), ("Reflection", "Good", 4.0)], "rubric order, label -> score"
    assert out["total_score"] == round((1 * 1 + 4 * 3) / 4, 2), "weighted by the rubric, not the model"
    assert out["max_score"] == 4 and out["overall_feedback"] == "Bona feina."
    assert out["diagnostics"] == {"matched": 2, "dropped": 1, "rubric_total": 2}


# --- the transcript -------------------------------------------------------------------

def test_the_transcript_carries_the_whole_build(client):
    headers, uid = launch()
    scope = (uid, "link-1", "lms.test")
    store.save_assistant(*scope, instructions="Sigues breu.", document_name="apunts.md",
                         document_text="x", tools=["calculator"], retrieval_enabled=1)
    t1 = store.start_turn(*scope, "Quant fa 17*23?", "off")
    trace = store.LocalTrace().save(*scope, "chat", {"tool_rounds": [
        {"name": "calculator", "args": "17*23", "ok": True, "result": "391", "call_text": ""}]})
    store.finish_turn(t1, "391", "qwen3.5:9b", trace)
    store.new_conversation(*scope)
    t2 = store.start_turn(*scope, "I ara?", "on")
    store.fail_turn(t2, "model_error", None)
    store.LocalTrace().save(*scope, "probe", {"query": "vapor", "results": [{}, {}]})
    store.save_reflection(*scope, {"q1": "Un tutor breu."}, submit=True)

    text = ev.build_transcript(*scope)
    for expected in ("Assistant instructions:\nSigues breu.", "Attached document: apunts.md",
                     "Document connected as a knowledge base: yes", "Selected tools: calculator",
                     "Student [knowledge base OFF, excerpts sent: 0]: Quant fa 17*23?",
                     "[tool call calculator(17*23) -> ok: 391]", "Assistant: 391",
                     "--- new conversation ---", "(no answer: model_error)",
                     "Test question 'vapor': 2 excerpt(s) listed",
                     "A: Un tutor breu.", "A: (not answered)"):
        assert expected in text, expected


def test_a_long_transcript_keeps_both_ends():
    text = "HEAD " + "x" * 20000 + " TAIL"
    out = ev.clamp_for_prompt(text, 1000)
    assert len(out) <= 1000 and out.startswith("HEAD") and out.endswith("TAIL")
    assert "conversation shortened" in out


# --- through the API ---------------------------------------------------------------------

def submit(client, headers):
    client.put("/workshop/api/reflection", headers=headers,
               json={"answers": {"q1": "Volia un tutor."}, "submit": True})


def test_feedback_needs_a_submission(client, lamb):
    headers, _ = launch()
    r = client.post("/workshop/api/feedback", headers=headers)
    assert (r.status_code, r.json()["code"]) == (409, "not_submitted")


def test_feedback_is_generated_stored_and_never_a_grade(client, lamb):
    headers, uid = launch()
    store.save_settings("link-1", "lms.test", rubric={
        "criteria": [{"name": "Reflection", "weight": 1, "levels": [{"label": "Good", "score": 4}]}]})
    submit(client, headers)
    lamb["replies"].append([json.dumps({"criteria": [
        {"criterion": "Reflection", "level": "Good", "feedback": "Concret."}],
        "overall_feedback": "Bé."})])
    body = client.post("/workshop/api/feedback", headers=headers).json()
    assert body["status"] == "completed" and body["total_score"] == 4.0 and body["max_score"] == 4
    assert body["criteria"][0]["feedback"] == "Concret." and body["model"] == "qwen3.5:9b"

    sent = lamb["requests"][0]
    assert sent["stream"] is False and "Criterion \"Reflection\"" in sent["messages"][0]["content"]
    assert "A: Volia un tutor." in sent["messages"][1]["content"]

    state = client.get("/workshop/api/state", headers=headers).json()
    assert state["feedback"]["overall_feedback"] == "Bé."
    with store.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM grades").fetchone()[0] == 0, "not a grade"
        kinds = [r[0] for r in conn.execute("SELECT kind FROM traces")]
    assert kinds == ["feedback"]


def test_without_a_teacher_rubric_the_default_one_is_used(client, lamb):
    headers, _ = launch()
    submit(client, headers)
    lamb["replies"].append(['{"criteria": [], "overall_feedback": "x"}'])
    body = client.post("/workshop/api/feedback", headers=headers).json()
    assert 'Criterion "Sources"' in lamb["requests"][0]["messages"][0]["content"]
    assert body["status"] == "failed" and "did not grade any criterion" in body["error_message"]


def test_a_model_error_is_a_failed_feedback_not_a_crash(client, lamb):
    headers, _ = launch()
    submit(client, headers)
    lamb["replies"].append(["[Ollama Error] API Error (400) for model qwen3.5:9b: Bad Request"])
    body = client.post("/workshop/api/feedback", headers=headers).json()
    assert body["status"] == "failed" and "shorter document" in body["hint"]
