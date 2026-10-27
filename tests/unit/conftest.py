"""Unit tests run without LAMB, the LMS or a running tool.

app/config.py reads the boot configuration at import time, so it is set here,
before any test module imports the app, with a throwaway database."""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="taller-ia-unit-")
os.environ.update({
    "ADMIN_USERNAME": "unit",
    "ADMIN_PASSWORD": "unit-password",
    "PUBLIC_BASE_URL": "http://tool.test",
    "TOOL_DB_PATH": os.path.join(_tmp, "tool.db"),
})

import pytest  # noqa: E402

from app import db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    """Each test gets an empty database at the current schema version."""
    if os.path.exists(os.environ["TOOL_DB_PATH"]):
        os.remove(os.environ["TOOL_DB_PATH"])
    for suffix in ("-wal", "-shm"):
        if os.path.exists(os.environ["TOOL_DB_PATH"] + suffix):
            os.remove(os.environ["TOOL_DB_PATH"] + suffix)
    db.init_db()
    yield


# --- helpers for the wizard API tests ----------------------------------------

import json  # noqa: E402

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import lamb_client, sessions, settings_store  # noqa: E402
from app.main import app as tool_app  # noqa: E402

ENGINE = "lamb_assistant.2"


@pytest.fixture
def client():
    with TestClient(tool_app) as c:
        yield c


def launch(name="Alumna", lti_user="u1", guid="lms.test", link="link-1", instructor=False,
           consent=True):
    uid = db.upsert_user(lti_user, guid, lti_user, name, f"{lti_user}@{guid}")
    db.save_activity(link, guid, "course-1", "Curs", "Taller", "unused", "", False, 3, uid)
    if consent:
        from app.workshop import service, store
        store.save_consent(uid, link, guid, service.CONSENT_VERSION)
    return {"X-Session-Token": sessions.create_session(uid, link, guid, instructor)}, uid


@pytest.fixture
def lamb(monkeypatch):
    """A fake LAMB: records requests, answers with the queued replies."""
    settings_store.set_value("lamb_api_base", "http://lamb.test")
    settings_store.set_value("lamb_api_key", "lamb_ak_test")
    settings_store.set_value("engine_model", ENGINE)
    fake = {"requests": [], "replies": []}

    def handler(request):
        body = json.loads(request.content)
        fake["requests"].append(body)
        deltas = fake["replies"].pop(0)
        if not body["stream"]:
            return httpx.Response(200, json={"model": "qwen3.5:9b", "choices": [
                {"message": {"role": "assistant", "content": "".join(deltas)},
                 "finish_reason": "stop"}]})
        lines = [json.dumps({"model": "qwen3.5:9b", "choices": [{"delta": {"content": d}}]})
                 for d in deltas]
        body = "".join(f"data: {line}\n\n" for line in lines) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=body.encode())

    monkeypatch.setattr(lamb_client, "_TRANSPORT", httpx.MockTransport(handler))
    return fake


def frames_of(response):
    out = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            payload = line[len("data: "):]
            out.append(payload if payload == "[DONE]" else json.loads(payload))
    return out


