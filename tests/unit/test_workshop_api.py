"""The wizard API end to end, with LAMB mocked (plan Phase 5)."""

import json

import pytest

from app import db, settings_store
from app.workshop import store

from conftest import ENGINE, frames_of, launch  # noqa: E402


def test_state_of_a_new_student(client, lamb):
    headers, _ = launch()
    s = client.get("/workshop/api/state", headers=headers).json()
    assert s["role"] == "student" and s["activity"]["configured"]
    assert s["assistant"] == {"instructions": "", "document": None, "retrieval_enabled": False,
                              "tools": [], "current_step": 1}
    assert s["turns"] == [] and s["engine"]["configured"]
    assert s["budget"]["input_limit"] == 24780


def test_requests_need_a_session(client):
    assert client.get("/workshop/api/state").status_code == 401
    assert client.get("/workshop/api/state",
                      headers={"X-Session-Token": "forged"}).status_code == 401


def test_save_assistant_fields(client):
    headers, _ = launch()
    r = client.put("/workshop/api/assistant", headers=headers, json={
        "instructions": "Respon en català.", "tools": ["calculator", 3], "current_step": 2,
        "owner": "ignored", "retrieval_enabled": "yes"})
    assert r.json()["instructions"] == "Respon en català."
    assert r.json()["tools"] == ["calculator"] and r.json()["current_step"] == 2
    assert r.json()["retrieval_enabled"] is False


def test_document_upload_and_remove(client):
    headers, _ = launch()
    text = "# Apunts\n\nLa fotosíntesi passa als cloroplasts.\r\n"
    r = client.post("/workshop/api/document", headers=headers,
                    files={"file": ("apunts.md", text.encode("utf-8"), "text/markdown")})
    doc = r.json()["document"]
    assert doc["name"] == "apunts.md" and doc["chars"] == len(text.strip().replace("\r\n", "\n"))
    assert doc["bytes"] == len(text.encode("utf-8")) and doc["tokens"] > 0
    assert client.delete("/workshop/api/document", headers=headers).json()["document"] is None


@pytest.mark.parametrize("name, status, code", [
    ("virus.exe", 422, "unsupported"),
    ("buit.txt", 422, "empty"),
    ("gran.txt", 413, "too_large"),
], ids=["unsupported", "empty", "too_large"])
def test_document_rejections(client, name, status, code):
    headers, _ = launch()
    data = {"unsupported": b"MZ", "empty": b"   \n",
            "too_large": b"x" * (10 * 1024 * 1024 + 1)}[code]
    r = client.post("/workshop/api/document", headers=headers, files={"file": (name, data)})
    assert (r.status_code, r.json()["code"]) == (status, code)


def test_reflection_is_saved_and_submitted_once(client):
    headers, _ = launch()
    r = client.put("/workshop/api/reflection", headers=headers,
                   json={"answers": {"q1": "Volia un tutor."}})
    assert r.json()["answers"] == {"q1": "Volia un tutor."} and r.json()["submitted_at"] is None
    first = client.put("/workshop/api/reflection", headers=headers,
                       json={"answers": {"q1": "Volia un tutor.", "q2": "El document."},
                             "submit": True}).json()["submitted_at"]
    again = client.put("/workshop/api/reflection", headers=headers,
                       json={"answers": {"q1": "Canviat"}, "submit": True}).json()
    assert first and again["submitted_at"] == first and again["answers"] == {"q1": "Canviat"}
    assert client.get("/workshop/api/state", headers=headers).json()["reflection"]["answers"] ==         {"q1": "Canviat"}
    assert client.put("/workshop/api/reflection", headers=headers,
                      json={"answers": ["no"]}).status_code == 400


def test_preview_shows_the_request_without_calling_lamb(client, lamb):
    headers, _ = launch()
    client.put("/workshop/api/assistant", headers=headers, json={"instructions": "Sigues breu."})
    frame = client.post("/workshop/api/preview", headers=headers, json={"message": "Hola"}).json()
    assert frame["type"] == "observability" and frame["data"]["phase"] == "preview"
    assert frame["data"]["request_body"] == {"model": ENGINE, "stream": True, "messages": [
        {"role": "system", "content": "Sigues breu."}, {"role": "user", "content": "Hola"}]}
    assert lamb["requests"] == []


def test_chat_turn_frames_storage_and_history(client, lamb):
    headers, uid = launch()
    client.put("/workshop/api/assistant", headers=headers, json={"instructions": "Sigues breu."})
    lamb["replies"] += [["Hola", "!"], ["Sí."]]

    first = frames_of(client.post("/workshop/api/chat", headers=headers,
                                  json={"message": "Hola", "rag_mode": "off"}))
    kinds = [f if f == "[DONE]" else f.get("type", "chunk") for f in first]
    assert kinds == ["observability", "chunk", "chunk", "observability", "[DONE]"]
    request, result = first[0]["data"], first[3]["data"]
    assert request["phase"] == "request" and result["phase"] == "result"
    assert request["request_body"] == lamb["requests"][0], "the panel shows what was sent"
    assert result["model"] == "qwen3.5:9b"

    client.post("/workshop/api/chat", headers=headers, json={"message": "Segur?"})
    assert lamb["requests"][1]["messages"] == [
        {"role": "system", "content": "Sigues breu."},
        {"role": "user", "content": "Hola"}, {"role": "assistant", "content": "Hola!"},
        {"role": "user", "content": "Segur?"}]

    turns = client.get("/workshop/api/state", headers=headers).json()["turns"]
    assert [(t["user"], t["assistant"], t["status"], t["model"]) for t in turns] == [
        ("Hola", "Hola!", "ok", "qwen3.5:9b"), ("Segur?", "Sí.", "ok", "qwen3.5:9b")]
    # Without a document searched in step 3 there is nothing to switch on (D3).
    assert turns[0]["rag_mode"] == "off" and turns[1]["rag_mode"] == "off"
    trace = client.get(f"/workshop/api/traces/{turns[0]['trace_id']}", headers=headers).json()
    assert trace["payload"] == lamb["requests"][0] and trace["model"] == "qwen3.5:9b"


def test_ollama_error_is_an_error_turn_not_an_answer(client, lamb):
    headers, _ = launch()
    lamb["replies"] += [["[Ollama Error] API Error 400"], ["D'acord."]]
    out = frames_of(client.post("/workshop/api/chat", headers=headers, json={"message": "Text"}))
    assert [f.get("type") for f in out] == ["observability", "error"]
    assert out[1]["data"]["type"] == "model_error" and "shorter document" in out[1]["data"]["hint"]
    assert "[Ollama Error]" not in json.dumps(out[1]), "the raw error text is not shown"

    client.post("/workshop/api/chat", headers=headers, json={"message": "Torna-hi"})
    assert lamb["requests"][1]["messages"] == [{"role": "user", "content": "Torna-hi"}], \
        "a failed turn is not history"
    turns = client.get("/workshop/api/state", headers=headers).json()["turns"]
    assert [(t["status"], t["error"]) for t in turns] == [("error", "model_error"), ("ok", None)]


def test_chat_needs_the_engine(client):
    headers, _ = launch()
    r = client.post("/workshop/api/chat", headers=headers, json={"message": "Hola"})
    assert (r.status_code, r.json()["code"]) == (409, "not_ready")


def test_chat_rejects_a_message_that_cannot_fit(client, lamb):
    headers, _ = launch()
    settings_store.set_value("engine_context_tokens", "4096")
    r = client.post("/workshop/api/chat", headers=headers, json={"message": "paraula " * 5000})
    assert (r.status_code, r.json()["code"]) == (413, "context_overflow")
    assert store.get_turns(*_scope(headers)) == [], "nothing was stored or sent"
    assert lamb["requests"] == []


def _scope(headers):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE token = ?",
                           (headers["X-Session-Token"],)).fetchone()
    return row["user_id"], row["resource_link_id"], row["consumer_guid"]


def test_traces_are_private(client, lamb):
    alice, _ = launch("Alice", "u1")
    bob, _ = launch("Bob", "u2")
    lamb["replies"].append(["Hola"])
    client.post("/workshop/api/chat", headers=alice, json={"message": "Secret"})
    tid = client.get("/workshop/api/state", headers=alice).json()["turns"][0]["trace_id"]
    assert client.get(f"/workshop/api/traces/{tid}", headers=bob).status_code == 404
    assert client.get("/workshop/api/state", headers=bob).json()["turns"] == []


def _signed_launch(roles, **extra):
    import base64, hashlib, hmac, time, urllib.parse, uuid
    settings_store.set_value("lti_consumer_key", "taller-ia")
    settings_store.set_value("lti_secret", "s3cret")
    params = {
        "lti_message_type": "basic-lti-launch-request", "lti_version": "LTI-1p0",
        "oauth_consumer_key": "taller-ia", "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())), "oauth_nonce": uuid.uuid4().hex,
        "oauth_version": "1.0", "resource_link_id": "link-1", "context_id": "course-1",
        "tool_consumer_instance_guid": "lms.test", "user_id": "u9", "roles": roles,
        "lis_person_name_full": "Alumna", **extra}
    enc = urllib.parse.urlencode(sorted(params.items()), quote_via=urllib.parse.quote)
    base = "&".join(["POST", urllib.parse.quote("http://tool.test/lti/launch", safe=""),
                     urllib.parse.quote(enc, safe="")])
    params["oauth_signature"] = base64.b64encode(
        hmac.new(b"s3cret&", base.encode(), hashlib.sha1).digest()).decode()
    return params


def test_student_launch_goes_to_the_wizard_in_the_lms_language(client, lamb):
    launch(lti_user="teacher", instructor=True)  # the activity exists
    r = client.post("/lti/launch", data=_signed_launch("Learner", launch_presentation_locale="ca-ES"),
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/workshop/?session=")
    assert r.headers["location"].endswith("&lang=ca-ES")


def test_a_new_conversation_drops_the_earlier_turns_from_the_history(client, lamb):
    headers, _ = launch()
    lamb["replies"] += [["Deu dies."], ["No ho sé."]]
    client.post("/workshop/api/chat", headers=headers, json={"message": "Quant de temps?"})
    assert client.post("/workshop/api/conversation/new", headers=headers).json() == {"conversation": 1}
    client.post("/workshop/api/chat", headers=headers, json={"message": "Quant de temps?"})
    assert lamb["requests"][1]["messages"] == [{"role": "user", "content": "Quant de temps?"}], \
        "the second run cannot read the first answer"
    state = client.get("/workshop/api/state", headers=headers).json()
    assert [t["conversation"] for t in state["turns"]] == [0, 1], "nothing is deleted"
    assert state["conversation"] == 1


def test_nothing_is_stored_or_sent_before_consent(client, lamb):
    headers, _ = launch(consent=False)
    state = client.get("/workshop/api/state", headers=headers).json()
    assert state["consent"]["accepted"] is False and state["consent"]["retention_days"] is None
    for method, path, kwargs in (
            ("put", "/workshop/api/assistant", {"json": {"instructions": "x"}}),
            ("post", "/workshop/api/document", {"files": {"file": ("a.md", b"x")}}),
            ("post", "/workshop/api/chat", {"json": {"message": "Hola"}}),
            ("put", "/workshop/api/reflection", {"json": {"answers": {}}}),
            ("post", "/workshop/api/feedback", {})):
        r = getattr(client, method)(path, headers=headers, **kwargs)
        assert r.status_code == 403, path
    assert lamb["requests"] == []

    assert client.put("/workshop/api/consent", headers=headers,
                      json={"version": "old"}).status_code == 409
    version = state["consent"]["version"]
    assert client.put("/workshop/api/consent", headers=headers, json={"version": version}).json() == {
        "accepted": True, "version": version}
    assert client.get("/workshop/api/state", headers=headers).json()["consent"]["accepted"] is True
    assert client.put("/workshop/api/assistant", headers=headers, json={"instructions": "x"}).status_code == 200


def test_the_notice_carries_the_teacher_note_and_the_retention(client, monkeypatch):
    monkeypatch.setenv("WORKSHOP_RETENTION_DAYS", "180")
    headers, _ = launch(consent=False)
    store.save_settings("link-1", "lms.test", consent_model_note="qwen3.5:9b, servidor propi")
    consent = client.get("/workshop/api/state", headers=headers).json()["consent"]
    assert consent["model_note"] == "qwen3.5:9b, servidor propi" and consent["retention_days"] == 180


def test_each_student_is_rate_limited(client, lamb, monkeypatch):
    monkeypatch.setenv("WORKSHOP_MESSAGES_PER_MINUTE", "2")
    alice, _ = launch("Alice", "u1")
    bob, _ = launch("Bob", "u2")
    lamb["replies"] += [["a"], ["b"], ["c"]]
    for _ in range(2):
        client.post("/workshop/api/chat", headers=alice, json={"message": "Hola"})
    r = client.post("/workshop/api/chat", headers=alice, json={"message": "Hola"})
    assert (r.status_code, r.json()["code"]) == (429, "rate_limited")
    assert len(lamb["requests"]) == 2, "the third message never reached LAMB"
    assert client.post("/workshop/api/chat", headers=bob, json={"message": "Hola"}).status_code == 200
