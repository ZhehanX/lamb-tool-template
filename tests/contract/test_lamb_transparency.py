"""Contract: an empty LAMB assistant passes the tool's messages through unchanged.

The observability panel shows the payload the tool sends to LAMB. That is only
what the model receives if LAMB's prompt processing adds nothing, which holds
for an assistant with an empty system prompt, an empty prompt template and
`no_rag` (simple_augment). This is undocumented LAMB behaviour, so it is
pinned here and must be re-run whenever the LAMB version changes.

The test talks to a twin of the engine assistant whose connector is `bypass`
with llm `debug-bypass`: instead of calling a model it answers with the
messages and body it would have sent, as
`Messages:\\n<json>\\n\\nBody:\\n<json>`.

Scope: this proves LAMB's prompt processing does not change the messages. It
cannot see past the connector (the model server, Ollama's context window),
which the context budget and the Phase 0 context check cover instead.

Run (needs a running LAMB):
  LAMB_CONTRACT=1 LAMB_CONTRACT_KEY=<creator key> \\
  LAMB_CONTRACT_DEBUG_MODEL=lamb_assistant.<id> pytest tests/contract

Optional: LAMB_CONTRACT_BASE (default http://localhost:9099);
LAMB_CONTRACT_TEMPLATE_WRAP=1 if the assistants' template is `{user_input}`,
in which case LAMB rewrites the last message to "\\n\\n" + text + "\\n\\n".
"""

import json
import os

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("LAMB_CONTRACT") != "1", reason="set LAMB_CONTRACT=1 to run against a live LAMB")

BASE = os.getenv("LAMB_CONTRACT_BASE", "http://localhost:9099").rstrip("/")
KEY = os.getenv("LAMB_CONTRACT_KEY", "")
DEBUG_MODEL = os.getenv("LAMB_CONTRACT_DEBUG_MODEL", "")
TEMPLATE_WRAP = os.getenv("LAMB_CONTRACT_TEMPLATE_WRAP") == "1"

SEPARATOR = "\n\nBody:\n"

# Whitespace the bypass stream would lose, Catalan and Spanish diacritics, a
# character outside the BMP, and a long message.
LONG = "\n".join(
    f"Paràgraf {i}: l'aigua  s'evapora, es condensa i torna a caure.\t·l·  ñ ¿? «» 🌱"
    for i in range(400))
CONVERSATION = [
    {"role": "system", "content": "  Ets un assistent del taller.\n\nRespon  en català.  "},
    {"role": "user", "content": "Hola!   Què és la fotosíntesi?\n\n"},
    {"role": "assistant", "content": "\nÉs el procés pel qual les plantes   fan servir la llum. "},
    {"role": "user", "content": "Resumeix aquest text:\n\n" + LONG + "\n   "},
]


@pytest.fixture(scope="module")
def client():
    if not KEY or not DEBUG_MODEL:
        pytest.fail("LAMB_CONTRACT_KEY and LAMB_CONTRACT_DEBUG_MODEL are required")
    with httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {KEY}"},
                      timeout=60) as c:
        yield c


def _debug_echo(client, body):
    """Send a non-streamed request and return (messages, body) as LAMB saw them."""
    r = client.post("/v1/chat/completions", json=body)
    assert r.status_code == 200, r.text[:500]
    content = r.json()["choices"][0]["message"]["content"]
    assert content.startswith("Messages:\n"), (
        f"{DEBUG_MODEL} did not answer like debug-bypass: {content[:200]!r}")
    head, sep, tail = content.partition(SEPARATOR)
    assert sep, "no Body section in the debug-bypass reply"
    return json.loads(head[len("Messages:\n"):]), json.loads(tail)


def _expected(messages):
    if not TEMPLATE_WRAP:
        return messages
    last = dict(messages[-1], content="\n\n" + messages[-1]["content"] + "\n\n")
    return messages[:-1] + [last]


def test_messages_reach_the_connector_unchanged(client):
    seen, _ = _debug_echo(client, {"model": DEBUG_MODEL, "messages": CONVERSATION,
                                   "stream": False})
    assert seen == _expected(CONVERSATION)


def test_facade_forwards_only_model_messages_stream(client):
    """The facade drops tools, sampling parameters and extra message fields.

    This is why tool calls use a text protocol and the tool cannot set the
    temperature."""
    messages = [{"role": "user", "content": "Quant fan 2 + 2?", "name": "alumne"}]
    seen, body = _debug_echo(client, {
        "model": DEBUG_MODEL, "messages": messages, "stream": False,
        "temperature": 0.1, "top_p": 0.5, "max_tokens": 5,
        "tools": [{"type": "function", "function": {"name": "calculator", "parameters": {}}}],
        "tool_choice": "auto",
    })
    assert set(body) == {"model", "messages", "stream"}
    assert seen == _expected([{"role": "user", "content": "Quant fan 2 + 2?"}])
    assert all(set(m) == {"role", "content"} for m in body["messages"])


def test_stream_is_server_sent_events(client):
    """Only the framing is checked: debug-bypass re-splits its text on
    whitespace when streaming, so the content is not comparable."""
    with client.stream("POST", "/v1/chat/completions", json={
            "model": DEBUG_MODEL, "messages": CONVERSATION[:2], "stream": True}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        data = [line[len("data: "):] for line in r.iter_lines() if line.startswith("data: ")]
    assert data and data[-1] == "[DONE]"
    chunks = [json.loads(d) for d in data[:-1]]
    assert all("delta" in c["choices"][0] for c in chunks)
    assert any(c["choices"][0]["delta"].get("content") for c in chunks)
