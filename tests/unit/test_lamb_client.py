"""LAMB failures become LambError, including Ollama errors that LAMB wraps
in a normal HTTP 200 answer (plan §0.1, v2.3)."""

import json

import httpx
import pytest

from app import lamb_client, settings_store
from app.lamb_client import LambError

# What LAMB 874f5cd + qwen3.5:9b actually return (Phase 0 step 7, check 3).
OVER_CONTEXT = "[Ollama Error] API Error (400) for model qwen3.5:9b: Bad Request"


@pytest.fixture(autouse=True)
def configured():
    settings_store.set_value("lamb_api_base", "http://lamb.test")
    settings_store.set_value("lamb_api_key", "lamb_ak_test")


def answer(content, model="qwen3.5:9b"):
    return httpx.Response(200, json={
        "id": "ollama-1", "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": -1, "completion_tokens": -1, "total_tokens": -1}})


def sse(*deltas, model="qwen3.5:9b"):
    lines = [json.dumps({"model": model, "choices": [{"index": 0, "delta": {"role": "assistant"}}]})]
    lines += [json.dumps({"model": model, "choices": [{"index": 0, "delta": {"content": d}}]})
              for d in deltas]
    body = "".join(f"data: {line}\n\n" for line in lines) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


def use(monkeypatch, response, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        return response
    monkeypatch.setattr(lamb_client, "_TRANSPORT", httpx.MockTransport(handler))


PAYLOAD = lamb_client.make_payload("lamb_assistant.2", [{"role": "user", "content": "Hola"}], False)
STREAM = lamb_client.make_payload("lamb_assistant.2", [{"role": "user", "content": "Hola"}], True)


def test_complete_returns_content_and_reported_model(monkeypatch):
    seen = []
    use(monkeypatch, answer("Hola!"), seen)
    c = lamb_client.complete(PAYLOAD)
    assert (c.content, c.model, c.finish_reason) == ("Hola!", "qwen3.5:9b", "stop")
    req = seen[0]
    assert req.url == "http://lamb.test/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer lamb_ak_test"
    assert req.content == lamb_client.encode_payload(PAYLOAD)


def test_complete_raises_on_wrapped_ollama_error(monkeypatch):
    use(monkeypatch, answer(OVER_CONTEXT))
    with pytest.raises(LambError) as e:
        lamb_client.complete(PAYLOAD)
    assert e.value.kind == "model_error"
    assert e.value.detail == OVER_CONTEXT
    assert "shorter document" in e.value.hint


def test_complete_timeout_hint(monkeypatch):
    use(monkeypatch, answer("[Ollama Error] Timeout after 120s for model qwen3.5:9b"))
    with pytest.raises(LambError) as e:
        lamb_client.complete(PAYLOAD)
    assert e.value.kind == "model_error" and "too long" in e.value.hint


@pytest.mark.parametrize("status, kind", [
    (401, "unauthorized"), (403, "forbidden"), (429, "rate_limited"),
    (422, "rejected"), (503, "unavailable"), (500, "http_error")])
def test_http_errors_are_mapped(monkeypatch, status, kind):
    traceback = 'Traceback (most recent call last):\n  File "/app/backend/main.py"'
    use(monkeypatch, httpx.Response(status, json={"detail": traceback}))
    for call, payload in ((lamb_client.complete, PAYLOAD),
                          (lambda p: list(lamb_client.stream(p)), STREAM)):
        with pytest.raises(LambError) as e:
            call(payload)
        assert (e.value.kind, e.value.status) == (kind, status)
        assert "Traceback" not in e.value.message, "LAMB's traceback is not for students"
        assert "Traceback" in e.value.detail


def test_unreachable(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("connection refused")
    monkeypatch.setattr(lamb_client, "_TRANSPORT", httpx.MockTransport(handler))
    with pytest.raises(LambError) as e:
        lamb_client.complete(PAYLOAD)
    assert e.value.kind == "unreachable"


def test_stream_yields_deltas_with_model(monkeypatch):
    use(monkeypatch, sse("La ", "fotosíntesi ", "és..."))
    deltas = list(lamb_client.stream(STREAM))
    assert "".join(d.content for d in deltas) == "La fotosíntesi és..."
    assert {d.model for d in deltas} == {"qwen3.5:9b"}


@pytest.mark.parametrize("deltas", [
    ("[Ollama Error] API Error 400",),               # what LAMB streams (Phase 0)
    ("[Oll", "ama Er", "ror] API Error 400"),          # the marker split across chunks
    ("[Ollama Error]",),
])
def test_stream_raises_before_showing_an_ollama_error(monkeypatch, deltas):
    use(monkeypatch, sse(*deltas))
    shown = []
    with pytest.raises(LambError) as e:
        for d in lamb_client.stream(STREAM):
            shown.append(d.content)
    assert shown == [], "nothing of the error text reaches the student"
    assert e.value.kind == "model_error"
    assert e.value.detail == "".join(deltas)


@pytest.mark.parametrize("deltas", [
    ("[1] Primer punt",),
    ("[", "Oll", "ie] no és un error"),
    ("[Ollama",),  # ends while still a possible prefix: a (strange) real answer
])
def test_stream_passes_answers_that_only_look_alike(monkeypatch, deltas):
    use(monkeypatch, sse(*deltas))
    assert "".join(d.content for d in lamb_client.stream(STREAM)) == "".join(deltas)


def test_ollama_error_text_later_in_an_answer_is_not_an_error(monkeypatch):
    text = "Si veus [Ollama Error] a la pantalla, torna-ho a provar."
    use(monkeypatch, answer(text))
    assert lamb_client.complete(PAYLOAD).content == text


def test_payload_kind_is_checked():
    with pytest.raises(ValueError):
        lamb_client.complete(STREAM)
    with pytest.raises(ValueError):
        list(lamb_client.stream(PAYLOAD))
