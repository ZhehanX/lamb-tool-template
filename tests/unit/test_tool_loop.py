"""Step 4: tools as a text protocol (plan T4, Phase 6), with LAMB mocked."""

import pytest

from app.workshop import service
from app.workshop.service import _marker_prefix
from app.workshop.tools import calculator
from conftest import frames_of, launch  # noqa: E402

CALL = '<tool name="calculator">17*23</tool>'


def chat(client, headers, message="Quant fa 17*23?"):
    return frames_of(client.post("/workshop/api/chat", headers=headers, json={"message": message}))


def kinds(frames):
    out = []
    for f in frames:
        if f == "[DONE]":
            out.append("[DONE]")
        elif f.get("type") == "tool_event":
            out.append(f"tool:{f['data']['type']}")
        else:
            out.append(f.get("type", "chunk"))
    return out


def events(frames, kind):
    return [f["data"] for f in frames if f != "[DONE]" and f.get("type") == "tool_event"
            and f["data"]["type"] == kind]


def shown_text(frames):
    return "".join(f["choices"][0]["delta"]["content"] for f in frames
                   if f != "[DONE]" and "choices" in f)


@pytest.fixture
def student(client):
    headers, _ = launch()
    client.put("/workshop/api/assistant", headers=headers,
               json={"instructions": "Sigues breu.", "tools": ["calculator"]})
    return headers


def test_the_tools_are_described_in_the_system_message(client, lamb, student):
    lamb["replies"].append(["Hola."])
    chat(client, student, "Hola")
    system = lamb["requests"][0]["messages"][0]["content"]
    assert system.startswith("Sigues breu.\n\n")
    assert '<tool name="TOOL_NAME">ARGUMENT</tool>' in system
    assert "- calculator: Evaluates an arithmetic expression" in system


def test_a_call_is_run_and_its_result_sent_back(client, lamb, student):
    lamb["replies"] += [["Ho calculo.\n", CALL, "\nI ara invento: 400"], ["Fa 391."]]
    out = chat(client, student)

    assert kinds(out) == ["observability", "chunk", "tool:tool", "tool:tool_done",
                          "observability", "chunk", "observability", "[DONE]"]
    [done] = events(out, "tool_done")
    assert (done["name"], done["success"], done["result"]) == ("calculator", True, "391")
    assert events(out, "tool")[0]["args"] == "17*23"
    # What the model wrote after its call was never read nor shown.
    assert shown_text(out) == "Ho calculo.\nFa 391."
    second = lamb["requests"][1]["messages"]
    assert second[-2] == {"role": "assistant", "content": "Ho calculo.\n" + CALL}
    assert second[-1] == {"role": "user",
                          "content": '<tool_result name="calculator">391</tool_result>'}
    assert out[4]["data"]["round"] == 1 and out[0]["data"]["round"] == 0


def test_the_turn_stores_what_the_student_saw_and_the_trace_keeps_the_rounds(client, lamb, student):
    lamb["replies"] += [[CALL], ["Fa 391."]]
    chat(client, student)
    turn = client.get("/workshop/api/state", headers=student).json()["turns"][0]
    assert (turn["status"], turn["assistant"]) == ("ok", "Fa 391.")
    trace = client.get(f"/workshop/api/traces/{turn['trace_id']}", headers=student).json()
    assert trace["tool_rounds"] == [{"name": "calculator", "args": "17*23", "ok": True,
                                     "result": "391", "call_text": CALL}]
    roles = [s["source"] for s in trace["segments"] if s["message"] >= 2]
    assert roles == ["tools", "tools"], "the call and the result are tagged as tool parts"


def test_the_next_turn_resends_the_rounds_as_they_were_sent(client, lamb, student):
    """T16: the call as the model wrote it and the result as it went back,
    then the last reply, without the text before the call a second time."""
    lamb["replies"] += [["Ho calculo.\n", CALL, "\nI ara invento: 400"], ["Fa 391."], ["782."]]
    chat(client, student)
    chat(client, student, "I el doble?")

    assert lamb["requests"][2]["messages"][1:] == [
        {"role": "user", "content": "Quant fa 17*23?"},
        {"role": "assistant", "content": "Ho calculo.\n" + CALL},
        {"role": "user", "content": '<tool_result name="calculator">391</tool_result>'},
        {"role": "assistant", "content": "Fa 391."},
        {"role": "user", "content": "I el doble?"}]
    turns = client.get("/workshop/api/state", headers=student).json()["turns"]
    assert turns[0]["assistant"] == "Ho calculo.\nFa 391.", "the chat keeps what the student saw"


def test_the_preview_shows_the_earlier_rounds(client, lamb, student):
    lamb["replies"] += [[CALL], ["Fa 391."]]
    chat(client, student)
    frame = client.post("/workshop/api/preview", headers=student, json={"message": "I ara?"}).json()
    tagged = [(s["source"], s["meta"]) for s in frame["data"]["segments"] if s["meta"]]
    assert tagged == [("history", {"tool_round": "call"}), ("history", {"tool_round": "result"})]


def test_a_marker_split_across_chunks_is_still_caught(client, lamb, student):
    lamb["replies"] += [["Un moment <", "to", 'ol name="calcul', 'ator">2+2</to', "ol>"], ["4"]]
    out = chat(client, student, "2+2?")
    assert shown_text(out) == "Un moment \n\n4"
    assert "<" not in shown_text(out)


def test_a_failing_tool_reports_the_error_to_the_model(client, lamb, student):
    lamb["replies"] += [['<tool name="calculator">1/0</tool>'], ["No es pot."]]
    out = chat(client, student, "1/0?")
    [done] = events(out, "tool_done")
    assert done["success"] is False and done["result"] == "division by zero"
    assert lamb["requests"][1]["messages"][-1]["content"] == (
        '<tool_result name="calculator" error="true">division by zero</tool_result>')


def test_an_unfinished_call_is_shown_as_the_answer(client, lamb, student):
    lamb["replies"].append(["Faig servir ", '<tool name="calculator">17*'])
    out = chat(client, student)
    assert "tool:tool_malformed" in kinds(out)
    assert shown_text(out) == 'Faig servir <tool name="calculator">17*'
    assert len(lamb["requests"]) == 1


def test_qwen_style_calls_work_too(client, lamb, student):
    lamb["replies"] += [['<tool_call>{"name": "calculator", "arguments": {"expression": "6*7"}}</tool_call>'],
                        ["42"]]
    out = chat(client, student, "6*7?")
    assert events(out, "tool")[0]["args"] == "6*7" and shown_text(out) == "42"


def test_at_most_three_rounds(client, lamb, student):
    lamb["replies"] += [[CALL], [CALL], [CALL], ["Encara " + CALL]]
    out = chat(client, student)
    assert [k for k in kinds(out) if k == "tool:tool"] == ["tool:tool"] * service.MAX_TOOL_ROUNDS
    assert len(lamb["requests"]) == service.MAX_TOOL_ROUNDS + 1
    assert shown_text(out).endswith("Encara " + CALL), "the last reply is shown as it is"


def test_without_tools_a_call_is_just_text(client, lamb):
    headers, _ = launch()
    lamb["replies"].append([CALL])
    out = chat(client, headers)
    assert shown_text(out) == CALL and "tool:tool" not in kinds(out)
    assert "TOOL_NAME" not in lamb["requests"][0]["messages"][0]["content"] if \
        lamb["requests"][0]["messages"][0]["role"] == "system" else True


def test_kb_search_is_not_offered_without_a_knowledge_base(client, lamb):
    headers, _ = launch()
    client.put("/workshop/api/assistant", headers=headers, json={"tools": ["kb_search"]})
    lamb["replies"].append(["Hola"])
    chat(client, headers, "Hola")
    assert lamb["requests"][0]["messages"] == [{"role": "user", "content": "Hola"}]


@pytest.mark.parametrize("text, n", [("abc <to", 3), ("abc <", 1), ("abc", 0), ("<tool", 0), ("x<t", 2)])
def test_marker_prefix(text, n):
    assert _marker_prefix(text) == n


@pytest.mark.parametrize("expr, ok, result", [
    ("17*23", True, "391"),
    ("(3 + 5) * 2", True, "16"),
    ("2^10", True, "1024"),
    ("7 × 6 ÷ 3", True, "14"),
    ("1/3", True, "0.3333333333"),
    ("1500 * 1.03 ** 4", True, "1688.263215"),
    ("10/4", True, "2.5"),
    ("9**9**9**9", False, "exponent larger than 100"),
    ("10**200", False, "exponent larger than 100"),
    ("1/0", False, "division by zero"),
    ("__import__('os')", False, "not allowed in an arithmetic expression: Call"),
    ("(-8) ** 0.5", False, "the result is not a real number"),
    ("", False, "no expression"),
    ("1+" * 150 + "1", False, "the expression is longer than 200 characters"),
])
def test_calculator(expr, ok, result):
    assert calculator(expr) == (ok, result)


def test_an_unclosed_qwen_call_at_the_end_of_the_reply_is_run(client, lamb, student):
    lamb["replies"] += [['<tool_call>{"name": "calculator", "arguments": {"input": "17 * 23"}}'], ["391"]]
    out = chat(client, student)
    assert events(out, "tool")[0]["args"] == "17 * 23"
    assert not events(out, "tool_malformed") and shown_text(out) == "391"
