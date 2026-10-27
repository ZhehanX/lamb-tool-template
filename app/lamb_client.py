"""Client for LAMB's OpenAI-compatible API.

Two calls are all this template needs:
  - list_models(): populate the instructor's assistant picker.
  - stream_chat(): relay a streamed completion to the student's browser.

The LAMB API key lives here, server-side, and NEVER reaches the browser.
Assistants are addressed by their model id, which LAMB shapes as
"lamb_assistant.<id>"."""

import json
from dataclasses import dataclass
from typing import Iterator

import httpx

from . import settings_store


class NotConfigured(RuntimeError):
    """Raised when the admin has not yet set the LAMB URL + key."""


def _lamb():
    base = settings_store.get("lamb_api_base")
    key = settings_store.get("lamb_api_key")
    if not base or not key:
        raise NotConfigured("LAMB URL and API key are not set — configure the tool at /admin.")
    return base.rstrip("/"), {"Authorization": f"Bearer {key}"}


def list_models() -> list[dict]:
    """Return [{id, name}] for the assistants this key may see."""
    base, headers = _lamb()
    url = f"{base}/v1/models"
    resp = httpx.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json().get("data", [])
    models = []
    for m in data:
        mid = m.get("id", "")
        # LAMB's /v1/models exposes no display name — derive one from the id
        # (strip the "lamb_assistant." prefix) so the picker reads cleanly.
        display = m.get("name") or mid.replace("lamb_assistant.", "assistant ")
        models.append({"id": mid, "name": display})
    return models


def stream_chat(model: str, messages: list[dict]) -> Iterator[str]:
    """Yield assistant text deltas from a streamed chat completion.

    Only model/messages/stream are sent — that is exactly what LAMB's
    facade forwards today, and all a chatbot needs."""
    base, headers = _lamb()
    url = f"{base}/v1/chat/completions"
    payload = {"model": model, "messages": messages, "stream": True}
    with httpx.stream("POST", url, headers=headers, json=payload, timeout=120) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
                delta = chunk["choices"][0]["delta"].get("content")
                if delta:
                    yield delta
            except (json.JSONDecodeError, KeyError, IndexError):
                continue


# --- workshop calls ------------------------------------------------------------
#
# The workshop builds the whole request itself and shows it in the panel, so
# the bytes sent must be the bytes shown: make_payload() + encode_payload()
# are the one place a request body is made, and the trace keeps the payload.
#
# Failures are raised as LambError, never returned as text. That includes a
# quirk of LAMB's Ollama connector: an Ollama failure (HTTP 400 for a prompt
# over the context window, a timeout, an empty reply) comes back as a normal
# HTTP 200 answer whose content starts with "[Ollama Error]". Left alone it
# would be stored and shown as the assistant's reply.

OLLAMA_ERROR = "[Ollama Error]"

# Read timeout above LAMB's own Ollama timeout (120 s by default), so a slow
# model surfaces as LAMB's "[Ollama Error] Timeout ..." rather than ours.
# Streams stay silent while a thinking model thinks: LAMB only sends chunks
# that carry content.
_TIMEOUT = httpx.Timeout(connect=10, read=180, write=30, pool=10)
_TRANSPORT = None  # tests swap in an httpx.MockTransport


class LambError(RuntimeError):
    """A call to LAMB that did not produce an answer.

    kind: unauthorized | forbidden | rate_limited | rejected | unavailable |
          http_error | unreachable | timeout | model_error
    message is safe to show a student; detail (LAMB's own text, possibly a
    traceback) is for the trace and the logs only."""

    def __init__(self, kind: str, message: str, status: int | None = None,
                 detail: str = "", hint: str = ""):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status
        self.detail = detail
        self.hint = hint

    def to_dict(self) -> dict:
        return {"kind": self.kind, "message": self.message, "status": self.status,
                "hint": self.hint}


@dataclass
class Completion:
    content: str
    model: str | None
    finish_reason: str | None


@dataclass
class Delta:
    content: str
    model: str | None


_HTTP_ERRORS = {
    401: ("unauthorized", "LAMB rejected the tool's API key (missing, revoked or expired)."),
    403: ("forbidden", "LAMB refused the request: API access is off for the organization, "
                       "or the key cannot use this assistant."),
    429: ("rate_limited", "LAMB is limiting requests right now. Try again in a moment."),
    422: ("rejected", "LAMB could not process the request."),
    503: ("unavailable", "LAMB or its model service is unavailable right now."),
}


def make_payload(model: str, messages: list[dict], stream: bool) -> dict:
    """The only fields LAMB's facade forwards (checked by tests/contract)."""
    return {"model": model, "messages": messages, "stream": stream}


def encode_payload(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _http() -> httpx.Client:
    return httpx.Client(timeout=_TIMEOUT, transport=_TRANSPORT)


def _http_error(resp: httpx.Response) -> LambError:
    kind, message = _HTTP_ERRORS.get(
        resp.status_code, ("http_error", f"LAMB answered with HTTP {resp.status_code}."))
    return LambError(kind, message, status=resp.status_code, detail=resp.text[:2000])


def model_error(content: str) -> LambError:
    """LambError for an answer that is really an "[Ollama Error] ..." text."""
    hint = ""
    if "Timeout" in content:
        hint = "The model took too long to answer."
    elif "400" in content:
        hint = ("The model service refused the request. This usually means the text "
                "is longer than the model can read: try a shorter document.")
    return LambError("model_error", "The model service returned an error instead of an answer.",
                     detail=content[:2000], hint=hint)


def _request(payload: dict):
    base, headers = _lamb()
    headers = {**headers, "Content-Type": "application/json"}
    return f"{base}/v1/chat/completions", headers, encode_payload(payload)


def _transport_error(e: httpx.HTTPError) -> LambError:
    if isinstance(e, httpx.TimeoutException):
        return LambError("timeout", "LAMB did not answer in time.", detail=str(e))
    return LambError("unreachable", "The tool could not reach LAMB.", detail=str(e))


def complete(payload: dict) -> Completion:
    """Non-streamed completion (contract tests, formative feedback)."""
    if payload.get("stream"):
        raise ValueError("complete() needs a payload with stream=False")
    url, headers, body = _request(payload)
    try:
        with _http() as client:
            resp = client.post(url, content=body, headers=headers)
    except httpx.HTTPError as e:
        raise _transport_error(e) from e
    if resp.status_code >= 400:
        raise _http_error(resp)
    data = resp.json()
    choice = data["choices"][0]
    content = choice["message"].get("content") or ""
    if content.startswith(OLLAMA_ERROR):
        raise model_error(content)
    return Completion(content, data.get("model"), choice.get("finish_reason"))


def _sse_chunks(resp: httpx.Response) -> Iterator[dict]:
    for line in resp.iter_lines():
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            return
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue


def stream(payload: dict) -> Iterator[Delta]:
    """Streamed completion as Delta(content, model) pieces.

    The opening text is held back until it can no longer be the start of
    "[Ollama Error]", so an error is raised before any of it reaches the
    student."""
    if not payload.get("stream"):
        raise ValueError("stream() needs a payload with stream=True")
    url, headers, body = _request(payload)
    try:
        with _http() as client, client.stream("POST", url, content=body, headers=headers) as resp:
            if resp.status_code >= 400:
                resp.read()
                raise _http_error(resp)
            head, model, decided = "", None, False
            chunks = _sse_chunks(resp)
            for chunk in chunks:
                model = chunk.get("model") or model
                try:
                    delta = chunk["choices"][0]["delta"].get("content")
                except (KeyError, IndexError):
                    continue
                if not delta:
                    continue
                if decided:
                    yield Delta(delta, model)
                    continue
                head += delta
                if OLLAMA_ERROR.startswith(head):
                    continue
                if head.startswith(OLLAMA_ERROR):
                    rest = "".join((c.get("choices") or [{}])[0].get("delta", {}).get("content") or ""
                                   for c in chunks)
                    raise model_error(head + rest)
                decided = True
                yield Delta(head, model)
            if not decided and head:
                if head == OLLAMA_ERROR:
                    raise model_error(head)
                yield Delta(head, model)
    except httpx.HTTPError as e:
        raise _transport_error(e) from e
