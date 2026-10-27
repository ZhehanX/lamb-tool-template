"""Server-sent event frames for the wizard (plan T6).

Frame types are the old branch's, so its panel and chat service port with
few changes: `observability`, `tool_event`, `tool_messages`, `error`, plus
the OpenAI-style content chunks relayed from LAMB.

The observability frame keeps the old fields the panel reads and adds the
trace (segments, budget, tokenizer). `request_body` is the payload exactly
as sent to LAMB: the panel never reconstructs it."""

import json

from .assembler import Trace


def observability(trace: Trace, phase: str, rag_mode: str) -> dict:
    """phase: "request" (sent before calling LAMB, so the panel shows the input
    while the model thinks) or "result" (after the answer, with the model
    LAMB reported)."""
    by_source: dict[str, list[str]] = {}
    for s in trace.segments:
        by_source.setdefault(s.source, []).append(s.text)
    selected = [c for c in (trace.retrieval or {}).get("candidates", []) if c["selected"]]
    return {
        "type": "observability",
        "data": {
            "phase": phase,
            "rag_mode": rag_mode,
            "system_instructions": "".join(by_source.get("instructions", [])),
            "user_input": "".join(by_source.get("user", [])),
            "rag_context": "\n\n".join(c["text"] for c in selected),
            "retrieved_sources": [
                {"document_id": c["source"], "chunk_id": c["rank"], "similarity": c["score"],
                 "content": c["text"][:500]} for c in selected],
            "final_llm_messages": trace.messages,
            "tools": [],  # the facade forwards no native tools (text protocol, T4)
            "request_body": trace.payload,
            "model": trace.model,
            "segments": [
                {"message": s.message, "role": s.role, "source": s.source, "text": s.text,
                 "tokens": s.tokens, "meta": s.meta} for s in trace.segments],
            "tokens_estimated": trace.tokens_estimated,
            "tokenizer": trace.tokenizer,
            "budget": trace.budget,
            "retrieval": trace.retrieval,
        },
    }


def error(message: str, kind: str = "internal_error", hint: str = "") -> dict:
    return {"type": "error", "data": {"message": message, "type": kind, "hint": hint}}


def sse(frame: dict) -> str:
    return f"data: {json.dumps(frame, ensure_ascii=False)}\n\n"
