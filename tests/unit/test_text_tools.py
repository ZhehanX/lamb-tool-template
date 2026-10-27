from app.workshop.ports import ToolSpec
from app.workshop.text_tools import TextToolProtocol

CALC = ToolSpec("calculator", "Evaluates arithmetic.", "an arithmetic expression")
P = TextToolProtocol()


def test_describe_lists_tools_in_the_chosen_format():
    text = P.describe([CALC])
    assert '<tool name="TOOL_NAME">ARGUMENT</tool>' in text
    assert "- calculator: Evaluates arithmetic. Argument: an arithmetic expression." in text
    assert "<tool_call>" in TextToolProtocol("qwen").describe([CALC])
    assert P.describe([]) == ""


def test_tag_call():
    reply = 'Ho calculo.\n<tool name="calculator"> 17 * 23 </tool>'
    call = P.find_call(reply)
    assert (call.name, call.arguments) == ("calculator", "17 * 23")
    assert reply[call.start:call.end] == call.raw


def test_qwen_call_with_object_or_string_arguments():
    for reply in ('<tool_call>{"name": "calculator", "arguments": {"expression": "2+2"}}</tool_call>',
                  '<tool_call>\n{"name": "calculator", "arguments": "{\\"input\\": \\"2+2\\"}"}\n</tool_call>'):
        call = P.find_call(reply)
        assert (call.name, call.arguments) == ("calculator", "2+2")


def test_first_call_wins():
    reply = ('<tool_call>{"name": "kb_search", "arguments": {"query": "x"}}</tool_call> '
             '<tool name="calculator">1+1</tool>')
    assert P.find_call(reply).name == "kb_search"


def test_malformed_calls_are_not_calls():
    for reply in ("Sense eines.", '<tool name="calculator">2+2', "<tool_call>{not json}</tool_call>",
                  '<tool_call>{"arguments": {}}</tool_call>', "<tool>2+2</tool>"):
        assert P.find_call(reply) is None, reply


def test_result_message():
    call = P.find_call('<tool name="calculator">1/0</tool>')
    assert P.format_result(call, "391", ok=True) == '<tool_result name="calculator">391</tool_result>'
    assert P.format_result(call, "division by zero", ok=False) == (
        '<tool_result name="calculator" error="true">division by zero</tool_result>')


def test_an_unclosed_qwen_call_counts_only_at_the_end_of_the_reply():
    reply = '<tool_call>{"name": "calculator", "arguments": {"input": "17 * 23"}}'
    assert P.find_call(reply) is None, "mid-stream the JSON may still grow"
    call = P.find_call(reply, at_end=True)
    assert (call.name, call.arguments, call.end) == ("calculator", "17 * 23", len(reply))
    assert P.find_call('<tool_call>{"name": "calculator", "arguments": ', at_end=True) is None
