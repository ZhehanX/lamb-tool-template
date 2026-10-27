"""Tool calls as plain text (plan T4).

LAMB's facade forwards no `tools` and has no `tool` role, so the model is told
in the system message how to write a call, the tool parses the call out of
the reply, runs it and sends the result back as the next user message. For
the student this is also the lesson: a tool call is text the model writes.

Two call formats are understood, whichever the instructions asked for:
  tag   <tool name="calculator">17*23</tool>                 (default)
  qwen  <tool_call>{"name": "calculator", "arguments": {"expression": "17*23"}}</tool_call>
The second is Qwen's native format. qwen3.5:9b often ends its reply right
after the JSON, without the closing `</tool_call>` (5 of 10 calls through
LAMB on 2026-10-22, and also when asked directly through Ollama), so at the
end of a reply an opening tag followed by a complete JSON object also counts
as a call. The default format needs no such allowance (20 of 20)."""

import json
import re

from .ports import ToolCall, ToolSpec

_TAG = re.compile(r'<tool\s+name\s*=\s*"([A-Za-z0-9_]+)"\s*>(.*?)</tool>', re.DOTALL)
_QWEN = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
_QWEN_UNCLOSED = re.compile(r"<tool_call>\s*(\{.*\})\s*$", re.DOTALL)

_INTRO = {
    "tag": (
        "You can use the tools listed below. To use one, write exactly\n"
        '<tool name="TOOL_NAME">ARGUMENT</tool>\n'
        "and stop. The result comes back in the next message as\n"
        '<tool_result name="TOOL_NAME">RESULT</tool_result>.\n'
        "Then continue your answer. Use a tool only when it helps.\n\nTools:"
    ),
    "qwen": (
        "You can use the tools listed below. To use one, write exactly\n"
        '<tool_call>{"name": "TOOL_NAME", "arguments": {"input": "ARGUMENT"}}</tool_call>\n'
        "and stop. The result comes back in the next message as\n"
        '<tool_result name="TOOL_NAME">RESULT</tool_result>.\n'
        "Then continue your answer. Use a tool only when it helps.\n\nTools:"
    ),
}


class TextToolProtocol:
    def __init__(self, style: str = "tag"):
        if style not in _INTRO:
            raise ValueError(f"unknown tool call style {style!r}")
        self.style = style

    def describe(self, tools: list[ToolSpec]) -> str:
        if not tools:
            return ""
        lines = [_INTRO[self.style]]
        for t in tools:
            lines.append(f"- {t.name}: {t.description} Argument: {t.argument}.")
        return "\n".join(lines)

    def find_call(self, text: str, at_end: bool = False) -> ToolCall | None:
        """at_end: `text` is the whole reply, so an unclosed <tool_call> whose
        JSON is complete counts too."""
        calls = []
        if m := _TAG.search(text):
            calls.append(ToolCall(m.group(1), m.group(2).strip(), m.group(0), m.start(), m.end()))
        for m in _QWEN.finditer(text):
            call = _qwen_call(m)
            if call:
                calls.append(call)
                break
        if not calls and at_end and (m := _QWEN_UNCLOSED.search(text)):
            call = _qwen_call(m)
            if call:
                calls.append(call)
        return min(calls, key=lambda c: c.start) if calls else None

    def format_result(self, call: ToolCall, result: str, ok: bool) -> str:
        error = "" if ok else ' error="true"'
        return f'<tool_result name="{call.name}"{error}>{result}</tool_result>'


def _qwen_call(m: re.Match) -> ToolCall | None:
    try:
        obj = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    name = obj.get("name") if isinstance(obj, dict) else None
    if not isinstance(name, str) or not name:
        return None
    args = obj.get("arguments", "")
    if isinstance(args, str):
        try:  # some models send the arguments object as a JSON string
            args = json.loads(args)
        except json.JSONDecodeError:
            pass
    if isinstance(args, dict):
        # The workshop's tools take one argument; accept any single key.
        args = next(iter(args.values())) if len(args) == 1 else json.dumps(args, ensure_ascii=False)
    return ToolCall(name, str(args).strip(), m.group(0), m.start(), m.end())
