"""The assembler: what the panel shows is what is sent (plan G4), and the
context budget (T14)."""

import json

import httpx
import pytest

from app import lamb_client, settings_store
from app.workshop import frames
from app.workshop.assembler import (LABELS, AssistantConfig, Budget, ContextOverflow,
                                    Document, ToolRound, build)
from app.workshop.ports import Chunk
from app.workshop.tokens import TokenCounter

COUNTER = TokenCounter()

HISTORY = [
    {"role": "user", "content": "Hola, qui ets?"},
    {"role": "assistant", "content": "Sóc el teu assistent del taller."},
]
CALL = '<tool name="calculator">17*23</tool>'
RESULT = '<tool_result name="calculator">391</tool_result>'
# An earlier turn that used a tool, as store.history gives it (T16).
TOOL_HISTORY = [
    {"role": "user", "content": "Quant fa 17*23?"},
    {"role": "assistant", "content": "Ho calculo.\n" + CALL, "tool_round": "call", "tool": "calculator"},
    {"role": "user", "content": RESULT, "tool_round": "result", "tool": "calculator"},
    {"role": "assistant", "content": "Fa 391."},
]
CHUNKS = [
    Chunk("La fotosíntesi passa als cloroplasts.", 0.82, "biologia.pdf", 1, True),
    Chunk("Les plantes absorbeixen CO2.", 0.74, "biologia.pdf", 2, True),
    Chunk("El cicle de l'aigua té quatre fases.", 0.31, "aigua.md", 3, False),
]
TOOLS_TEXT = 'You can use the tools listed below.\n- calculator: ...'


def full_config(doc_text="Text del document.\n\nSegon paràgraf."):
    return AssistantConfig(instructions="Respon sempre en català.",
                           document=Document("apunts.md", doc_text),
                           tools_text=TOOLS_TEXT)


def assert_segments_rebuild_messages(messages, trace):
    assert trace.messages is messages
    for i, m in enumerate(messages):
        assert "".join(s.text for s in trace.segments if s.message == i) == m["content"]
        assert {s.role for s in trace.segments if s.message == i} == {m["role"]}
    assert all(s.text for s in trace.segments), "empty segments are dropped"


@pytest.mark.parametrize("config, history, retrieved, rounds", [
    (AssistantConfig(), [], [], []),
    (AssistantConfig(instructions="Només instruccions."), [], [], []),
    (AssistantConfig(document=Document("d.txt", "Només document.")), [], [], []),
    (AssistantConfig(tools_text=TOOLS_TEXT), [], [], []),
    (full_config(), HISTORY, CHUNKS, []),
    (full_config(), HISTORY, CHUNKS, [ToolRound(CALL, RESULT)]),
    (full_config(), TOOL_HISTORY + HISTORY, CHUNKS, [ToolRound(CALL, RESULT)]),
])
def test_segments_rebuild_every_message(config, history, retrieved, rounds):
    messages, trace = build(config, history, "Què és la fotosíntesi?", retrieved, rounds)
    assert_segments_rebuild_messages(messages, trace)
    assert messages[-1]["role"] == "user"
    assert trace.tokens_estimated == COUNTER.count_messages(messages)


def test_layout():
    rounds = [ToolRound('<tool name="calculator">17*23</tool>',
                        '<tool_result name="calculator">391</tool_result>')]
    messages, trace = build(full_config(), HISTORY, "Quant fa 17*23?", CHUNKS, rounds)
    roles = [m["role"] for m in messages]
    assert roles == ["system", "user", "assistant", "user", "assistant", "user"]
    system = messages[0]["content"]
    assert system.startswith("Respon sempre en català.\n\n## Document: apunts.md\n\n")
    assert system.endswith(TOOLS_TEXT)
    assert messages[1:3] == HISTORY
    question = messages[3]["content"]
    assert question.startswith(LABELS["retrieval"])
    assert question.endswith(LABELS["question"] + "Quant fa 17*23?")
    assert "cloroplasts" in question and "CO2" in question
    assert "quatre fases" not in question, "unselected candidates are not sent"
    assert [c["rank"] for c in trace.retrieval["candidates"]] == [1, 2, 3]
    assert messages[4]["content"] == rounds[0].call
    assert messages[5]["content"] == rounds[0].result


def test_earlier_tool_rounds_are_sent_as_they_were():
    """T16: a later turn sees the call and the result, tagged for the panel."""
    messages, trace = build(AssistantConfig(), TOOL_HISTORY, "I per 2?")
    assert messages == [{"role": m["role"], "content": m["content"]} for m in TOOL_HISTORY] + [
        {"role": "user", "content": "I per 2?"}]
    rounds = [(s.message, s.source, s.meta) for s in trace.segments if s.meta]
    assert rounds == [(1, "history", {"tool_round": "call"}), (2, "history", {"tool_round": "result"})]
    assert trace.budget["tool_results_elided"] == 0


def test_empty_config_sends_only_the_message():
    messages, trace = build(AssistantConfig(), [], "Hola")
    assert messages == [{"role": "user", "content": "Hola"}]
    assert [s.source for s in trace.segments] == ["user"]
    assert trace.budget["document"] is None


def test_no_leading_separator_without_instructions():
    messages, _ = build(AssistantConfig(document=Document("d.md", "Text.")), [], "Hola")
    assert messages[0]["content"] == "## Document: d.md\n\nText."


def test_panel_shows_the_bytes_that_are_sent(monkeypatch):
    """G4: the observability frame's request_body is the payload the tool
    posts to LAMB, byte for byte."""
    settings_store.set_value("lamb_api_base", "http://lamb.test")
    settings_store.set_value("lamb_api_key", "lamb_ak_test")
    sent = []

    def handler(request):
        sent.append(request.content)
        return httpx.Response(200, json={"model": "qwen3.5:9b", "choices": [
            {"message": {"role": "assistant", "content": "D'acord."}, "finish_reason": "stop"}]})

    monkeypatch.setattr(lamb_client, "_TRANSPORT", httpx.MockTransport(handler))
    messages, trace = build(full_config(), HISTORY, "Explica-ho  amb  espais ", CHUNKS)
    trace.payload = lamb_client.make_payload("lamb_assistant.2", messages, stream=False)
    frame = frames.observability(trace, phase="request", rag_mode="on")

    lamb_client.complete(trace.payload)

    assert sent == [lamb_client.encode_payload(frame["data"]["request_body"])]
    assert json.loads(sent[0]) == {"model": "lamb_assistant.2", "messages": messages,
                                   "stream": False}
    # The frame survives the SSE encoding unchanged.
    assert json.loads(frames.sse(frame)[len("data: "):]) == frame


def test_frame_keeps_the_old_panel_fields():
    messages, trace = build(full_config(), HISTORY, "Pregunta", CHUNKS)
    data = frames.observability(trace, "result", "on")["data"]
    for key in ("system_instructions", "user_input", "rag_context", "retrieved_sources",
                "final_llm_messages", "tools", "request_body", "phase", "rag_mode"):
        assert key in data
    assert data["system_instructions"] == "Respon sempre en català."
    assert data["user_input"] == "Pregunta"
    assert [s["document_id"] for s in data["retrieved_sources"]] == ["biologia.pdf"] * 2
    assert data["final_llm_messages"] == messages


# --- budget (T14) -----------------------------------------------------------

LONG_DOC = "\n\n".join(f"Paràgraf {i}: el text de l'alumne continua amb més frases." for i in range(3000))


def test_document_fits_untouched_under_the_budget():
    messages, trace = build(full_config(), HISTORY, "Hola", budget=Budget())
    doc = trace.budget["document"]
    assert doc["truncated"] is False and doc["kept_chars"] == doc["total_chars"]
    assert "Document truncated" not in messages[0]["content"]


def test_long_document_is_cut_to_the_budget():
    budget = Budget(context_tokens=8192, reserve_output=1024, margin=0.15)
    messages, trace = build(full_config(LONG_DOC), HISTORY, "Resumeix", budget=budget)

    assert trace.tokens_estimated <= budget.input_limit
    doc = trace.budget["document"]
    assert doc["truncated"] is True and 0 < doc["kept_chars"] < doc["total_chars"]
    kept = next(s.text for s in trace.segments if s.source == "document")
    assert LONG_DOC.startswith(kept) and len(kept) == doc["kept_chars"]
    note = LABELS["document_truncated"].format(kept=len(kept), total=len(LONG_DOC))
    assert note in messages[0]["content"]
    assert messages[0]["content"].endswith(TOOLS_TEXT), "instructions and tools are never cut"
    assert trace.budget["history_dropped"] == 0
    # Close to the limit, not cut far more than needed.
    assert trace.tokens_estimated > budget.input_limit - 300
    assert_segments_rebuild_messages(messages, trace)


def test_history_is_dropped_only_when_the_document_cannot_make_room():
    history = []
    for i in range(30):
        history += [{"role": "user", "content": f"Pregunta {i} " + "bla " * 60},
                    {"role": "assistant", "content": f"Resposta {i} " + "bla " * 120}]
    budget = Budget(context_tokens=8192, reserve_output=1024, margin=0.15)
    messages, trace = build(full_config(LONG_DOC), history, "Última", budget=budget)

    assert trace.tokens_estimated <= budget.input_limit
    dropped = trace.budget["history_dropped"]
    assert dropped > 0 and dropped % 2 == 0, "whole exchanges are dropped"
    # The document only gets the room left over by the last dropped exchange:
    # had one exchange fewer been dropped, nothing of it would have fit.
    doc = trace.budget["document"]
    exchange = COUNTER.count_messages(history[dropped - 2:dropped]) - COUNTER.prompt_overhead
    assert doc["truncated"] and doc["kept_tokens"] < exchange
    assert messages[1]["content"] == history[dropped]["content"], "the oldest go first"
    assert messages[-1]["content"] == "Última"
    assert_segments_rebuild_messages(messages, trace)


def search_turn(i, result_words=400):
    """An earlier turn whose kb_search brought back a long result."""
    result = f'<tool_result name="kb_search">{"fragment " * result_words}{i}</tool_result>'
    return [{"role": "user", "content": f"Pregunta {i}"},
            {"role": "assistant", "content": f'<tool name="kb_search">tema {i}</tool>',
             "tool_round": "call", "tool": "kb_search"},
            {"role": "user", "content": result, "tool_round": "result", "tool": "kb_search"},
            {"role": "assistant", "content": f"Resposta {i}"}]


def words(n_tokens, word="paraula "):
    """Text of about n_tokens."""
    return word * max(1, round(n_tokens * 100 / COUNTER.count(word * 100)))


def test_earlier_tool_results_give_way_before_the_document():
    """T16: the oldest results become a short note first; the document is
    only cut once no result is left to shorten."""
    history = search_turn(0) + search_turn(1) + search_turn(2)
    budget = Budget(context_tokens=8192, reserve_output=1024, margin=0.15)
    rounds = [ToolRound(CALL, "<tool_result>" + "x " * 300 + "</tool_result>")]
    one_result = COUNTER.count(history[2]["content"])
    rest = COUNTER.count_messages(build(full_config(""), history, "Última", tool_rounds=rounds)[0])
    # Over the limit by about one and a half results.
    doc = words(budget.input_limit - rest + one_result * 3 // 2, "Text de l'alumne. ")
    messages, trace = build(full_config(doc), history, "Última", tool_rounds=rounds, budget=budget)

    assert trace.tokens_estimated <= budget.input_limit
    elided = trace.budget["tool_results_elided"]
    assert elided == 2, "only as many as needed"
    note = LABELS["result_elided"].format(name="kb_search")
    results = [m["content"] for m in messages if m["content"].startswith("<tool_result name=\"kb_search\"")]
    assert results[:elided] == [note] * elided, "the oldest go first"
    assert all(r != note for r in results[elided:])
    assert trace.budget["document"]["truncated"] is False
    assert trace.budget["history_dropped"] == 0
    assert "x x x" in messages[-1]["content"], "this turn's own rounds are never shortened"
    notes = [s for s in trace.segments if s.meta.get("elided")]
    assert [s.source for s in notes] == ["scaffold"] * elided
    assert_segments_rebuild_messages(messages, trace)


def test_a_short_result_is_not_replaced_by_a_longer_note():
    budget = Budget(context_tokens=2048, reserve_output=512, margin=0.15)
    history = TOOL_HISTORY + search_turn(1)
    rest = COUNTER.count_messages(build(AssistantConfig(), history, "x")[0])
    message = words(budget.input_limit - rest + 100)
    messages, trace = build(AssistantConfig(), history, message, budget=budget)
    assert trace.budget["tool_results_elided"] == 1
    assert RESULT in [m["content"] for m in messages], "the calculator's result stays"
    assert trace.budget["history_dropped"] == 0


def test_an_exchange_is_dropped_with_its_tool_rounds():
    history = []
    for i in range(60):
        history += search_turn(i, result_words=150)
    budget = Budget(context_tokens=4096, reserve_output=512, margin=0.15)
    messages, trace = build(AssistantConfig(instructions="Sigues breu."), history, "Última",
                            budget=budget)

    assert trace.tokens_estimated <= budget.input_limit
    assert trace.budget["tool_results_elided"] == 60, "every result gave way first"
    dropped = trace.budget["history_dropped"]
    assert dropped > 0 and dropped % 4 == 0, "whole exchanges, rounds included"
    assert messages[1]["content"] == history[dropped]["content"]
    assert messages[1]["role"] == "user" and messages[2]["content"].startswith("<tool name=")
    assert_segments_rebuild_messages(messages, trace)


def test_overflow_when_the_message_alone_is_too_long():
    budget = Budget(context_tokens=2048, reserve_output=512, margin=0.15)
    with pytest.raises(ContextOverflow) as e:
        build(AssistantConfig(), HISTORY, "paraula " * 3000, budget=budget)
    assert e.value.limit == budget.input_limit and e.value.needed > budget.input_limit


def test_budget_numbers():
    assert Budget(context_tokens=32768, reserve_output=3072, margin=0.15).input_limit == 24780


# --- the step-5 cap (T18) ---------------------------------------------------

def test_a_cap_only_lowers_the_limit():
    assert Budget(input_cap=2000).input_limit == 2000
    assert Budget(context_tokens=4096, reserve_output=512, input_cap=8000).input_limit == 2969


def test_the_cap_cuts_for_real_and_is_recorded():
    doc = words(3000, "Text de l'alumne. ")
    messages, trace = build(full_config(doc), HISTORY, "Resumeix", budget=Budget(input_cap=2000))

    assert trace.tokens_estimated <= 2000
    assert trace.budget["input_cap"] == 2000 and trace.budget["input_limit"] == 2000
    assert trace.budget["document"]["truncated"] is True
    assert_segments_rebuild_messages(messages, trace)
