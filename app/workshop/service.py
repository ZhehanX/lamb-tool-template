"""The wizard's server side: turning stored state into a request, and the
chat turn itself.

A chat turn (plan Phase 5 step 3, Phase 6):
  1. assemble the request from the student's assistant + history (T3)
  2. send the `observability` "request" frame BEFORE calling LAMB, so the
     panel shows the input while the model is still thinking
  3. relay the answer from LAMB as content chunks
  4. if the model wrote a tool call (text protocol, T4): stop reading, run
     the tool, send `tool_event` frames, and call LAMB again with the call
     and its result appended — at most MAX_TOOL_ROUNDS times
  5. store the turn and its trace (with the model LAMB reported), and send
     the "result" frame
A failure (LambError, including the wrapped "[Ollama Error]" answers) ends
the stream with an `error` frame and is stored as a failed turn, which never
becomes history the model sees again.

The tool rounds become history for later turns too (T16): each call as the
model wrote it and each result as it went back, then the model's last reply.
The chat shows what the student saw, stored apart."""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from .. import lamb_client, settings_store
from ..lamb_client import LambError
from . import evaluation, frames, kb_client, retention, store, tools
from .assembler import AssistantConfig, Budget, ContextOverflow, Document, ToolRound, build
from .text_tools import TextToolProtocol
from .tokens import TokenCounter

logger = logging.getLogger("workshop")

DEFAULT_CONTEXT_TOKENS = 32768
# Version of the consent notice (frontend workshop.consent_* strings). Bump it
# when what students are told changes materially, so they are asked again.
CONSENT_VERSION = "taller-ia-2026-10-24"  # T15: the document also goes to the kb-server
MAX_TOOL_ROUNDS = 3
# Every call format starts with this ("<tool name=" and "<tool_call>").
CALL_MARKER = "<tool"

traces = store.LocalTrace()
# tag (default) or qwen; see text_tools.py. On qwen3.5:9b both reached 10/10
# correct calls, and qwen answered about four times faster (Phase 6, §9).
protocol = TextToolProtocol(os.getenv("WORKSHOP_TOOL_CALL_STYLE", "tag"))


class NotReady(Exception):
    """The deployment or the activity is not set up for this action."""


class RateLimited(Exception):
    """Too many messages: LAMB's facade checks no quota and Ollama records no
    usage, so the tool limits each student itself (plan §8)."""


def _limit(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def check_rate(uid: int, rlid: str, guid: str) -> None:
    now = datetime.now(timezone.utc)
    for window, env, default, message in (
            (timedelta(minutes=1), "WORKSHOP_MESSAGES_PER_MINUTE", 6,
             "You are sending messages very fast. Wait a minute and try again."),
            (timedelta(days=1), "WORKSHOP_MESSAGES_PER_DAY", 200,
             "You have reached today's message limit for this activity.")):
        limit = _limit(env, default)
        since = (now - window).isoformat(timespec="seconds")
        if limit > 0 and store.turns_since(uid, rlid, guid, since) >= limit:
            raise RateLimited(message)


def has_consented(session) -> bool:
    row = store.get_consent(*scope(session))
    return row is not None and row["accepted"] == 1 and row["version"] == CONSENT_VERSION


def scope(session) -> tuple[int, str, str]:
    return session["user_id"], session["resource_link_id"], session["consumer_guid"]


def engine_model() -> str:
    model = settings_store.get("engine_model")
    if not model:
        raise NotReady("The workshop engine is not configured: set it on the tool's /admin page.")
    return model


def budget() -> Budget:
    value = settings_store.get("engine_context_tokens")
    return Budget(context_tokens=int(value) if value else DEFAULT_CONTEXT_TOKENS)


def counter() -> TokenCounter:
    return TokenCounter()


# Excerpts placed in the prompt per search (step 3 and kb_search).
RETRIEVAL_K = 3

# Step 3's knowledge base is the student's own step-2 document, split and
# embedded in the tool's kb-server (plan T15). kb_status says where it is:
#   unavailable  no kb-server configured for this deployment
#   none         no document attached, so nothing to search
#   processing   being split and embedded (the kb-server ingests in the background)
#   ready        searchable
#   failed       could not be split or embedded
#   unreachable  the kb-server does not answer
_FILE_STATUS = {"completed": "ready", "failed": "failed", "cancelled": "failed", "deleted": "failed"}


def _kb(uid: int, rlid: str, guid: str) -> tuple[dict, int | None]:
    """(what kb_status reports, the collection id when it is ready)."""
    if not kb_client.configured():
        return {"status": "unavailable"}, None
    row = store.get_assistant(uid, rlid, guid)
    if row is None or row["document_text"] is None:
        return {"status": "none"}, None
    collection = store.student_collection(uid, rlid, guid)
    if collection is None:
        return {"status": "failed"}, None
    try:
        files = kb_client.list_files(collection["collection_id"])
    except kb_client.KbError:
        return {"status": "unreachable"}, None
    if not files:
        return {"status": "failed"}, None
    status = _FILE_STATUS.get(files[0].get("status"), "processing")
    chunks = files[0].get("document_count") or 0
    if status == "ready" and not chunks:
        status = "failed"
    view = {"status": status, "chunks": chunks, "chunk_size": collection["chunk_size"],
            "chunk_overlap": collection["chunk_overlap"]}
    return view, collection["collection_id"] if status == "ready" else None


def kb_status(uid: int, rlid: str, guid: str) -> dict:
    return _kb(uid, rlid, guid)[0]


def retriever(uid: int, rlid: str, guid: str):
    """The student's document as a knowledge base once it is searchable, or None."""
    collection_id = _kb(uid, rlid, guid)[1]
    return kb_client.KbServerRetriever(collection_id) if collection_id is not None else None


def index_document(uid: int, rlid: str, guid: str, name: str, text: str) -> None:
    """Make the step-2 document searchable for step 3 (T15): its extracted
    text, the same the context gets, in a fresh collection; the previous
    document's goes to kb_trash. A failure is only logged: kb_status reports
    it, and the document still works whole."""
    retention.drop_student_kb(uid, rlid, guid)
    if not kb_client.configured():
        return
    try:
        collection_id = kb_client.create_collection(
            kb_client.student_collection_name(guid, rlid, uid), f"taller-ia:{guid}:{rlid}:{uid}")
        info = kb_client.get_collection(collection_id) or {}
        store.add_student_collection(uid, rlid, guid, collection_id,
                                     info.get("embeddings_model") or {},
                                     kb_client.CHUNK_SIZE, kb_client.CHUNK_OVERLAP)
        kb_client.ingest_text(collection_id, name, text)
    except kb_client.KbError as e:
        logger.warning("document of user %s not indexed: %s (%s)", uid, e, e.detail[:300])


def enabled_tools(row, has_kb: bool) -> list:
    names = json.loads(row["tools"] or "[]") if row is not None else []
    specs = []
    if "calculator" in names:
        specs.append(tools.CALCULATOR)
    if "kb_search" in names and has_kb:
        specs.append(tools.KB_SEARCH)
    return specs


def run_tool(name: str, argument: str, kb=None) -> tuple[bool, str]:
    if name == "calculator":
        return tools.calculator(argument)
    if name == "kb_search" and kb is not None:
        try:
            return tools.kb_search(kb, argument, RETRIEVAL_K)
        except kb_client.KbError as e:
            return False, str(e)
    return False, f"there is no tool called {name!r}"


def config_from(row, has_kb: bool = False, whole_document: bool = True) -> AssistantConfig:
    if row is None:
        return AssistantConfig()
    doc = None
    if row["document_text"] is not None and whole_document:
        doc = Document(row["document_name"] or "document", row["document_text"])
    return AssistantConfig(instructions=row["instructions"], document=doc,
                           tools_text=protocol.describe(enabled_tools(row, has_kb)))


def _design(row, kb, rag_mode: str):
    """(config, rag_mode, the retriever kb_search may use) for one request.

    Connected in step 3 and switched on (D3), the document is searched instead
    of sent whole (T15). Switched off, the whole document goes and kb_search
    is withdrawn too, so the two runs differ in that one thing. Not connected,
    the document goes whole and the step-4 kb_search tool still works."""
    connected = kb is not None and row is not None and bool(row["retrieval_enabled"])
    mode = rag_mode if connected else "off"
    tool_kb = None if connected and mode == "off" else kb
    return config_from(row, tool_kb is not None, whole_document=mode == "off"), mode, tool_kb


_NOT_SEARCHABLE = {
    "none": "Attach a document in step 2 first: this step searches it.",
    "processing": "Your document is still being prepared for searching. Try again in a moment.",
    "failed": "Your document could not be prepared for searching. Remove it in step 2 and attach it again.",
    "unavailable": "This workshop has no knowledge base server.",
    "unreachable": "The knowledge base server cannot be reached right now.",
}


def probe(session, query_text: str) -> dict:
    """Step 3: what a test question retrieves from the student's document,
    every candidate included, without calling the model. Saved as a trace."""
    uid, rlid, guid = scope(session)
    kb = retriever(uid, rlid, guid)
    if kb is None:
        raise NotReady(_NOT_SEARCHABLE.get(kb_status(uid, rlid, guid)["status"],
                                           "Your document cannot be searched."))
    chunks = kb.search(query_text, RETRIEVAL_K)
    result = {**kb.last, "results": [
        {"rank": c.rank, "similarity": c.score, "data": c.text, "source": c.source,
         "selected": c.selected, "meta": c.meta} for c in chunks]}
    traces.save(uid, rlid, guid, "probe", result)
    return result


def assistant_view(row) -> dict:
    """The student's assistant as the wizard reads it."""
    if row is None:
        return {"instructions": "", "document": None, "retrieval_enabled": False,
                "tools": [], "current_step": 1}
    doc = None
    if row["document_text"] is not None:
        b, c = budget(), counter()
        tokens = c.count(row["document_text"])
        doc = {"name": row["document_name"], "bytes": row["document_bytes"],
               "chars": len(row["document_text"]), "tokens": tokens,
               "input_limit": b.input_limit, "tokenizer": c.name}
    return {"instructions": row["instructions"], "document": doc,
            "retrieval_enabled": bool(row["retrieval_enabled"]),
            "tools": json.loads(row["tools"] or "[]"), "current_step": row["current_step"]}


def turn_view(t) -> dict:
    return {"id": t["id"], "user": t["user_content"], "assistant": t["assistant_content"],
            "status": t["status"], "error": t["error"], "rag_mode": t["rag_mode"],
            "model": t["model"], "trace_id": t["trace_id"], "created_at": t["created_at"],
            "retrieved": t["retrieved"] if "retrieved" in t.keys() else 0,
            "conversation": t["conversation"]}


def preview(session, user_input: str, rag_mode: str = "off") -> dict:
    """The request the next message would make, without calling LAMB, so
    steps 1-4 can show what the assistant receives as it is being built."""
    uid, rlid, guid = scope(session)
    # With the knowledge base on, the excerpts depend on the message sent, so
    # the preview has none.
    config, rag_mode, _ = _design(store.get_assistant(uid, rlid, guid),
                                  retriever(uid, rlid, guid), rag_mode)
    messages, trace = build(config, store.history(uid, rlid, guid), user_input,
                            budget=budget(), counter=counter())
    model = settings_store.get("engine_model") or "(engine not configured)"
    trace.payload = lamb_client.make_payload(model, messages, stream=True)
    return frames.observability(trace, phase="preview", rag_mode=rag_mode)


class _Turn:
    """Everything one chat turn needs to assemble each of its requests."""

    def __init__(self, session, message: str, rag_mode: str):
        uid, rlid, guid = scope(session)
        self.scope = (uid, rlid, guid)
        self.model = engine_model()
        # The A/B switch (D3) only means something with the document
        # connected in step 3; self.kb is what kb_search may use.
        self.config, self.rag_mode, self.kb = _design(store.get_assistant(uid, rlid, guid),
                                                      retriever(uid, rlid, guid), rag_mode)
        self.tools_on = bool(self.config.tools_text)
        self.history = store.history(uid, rlid, guid)
        self.message = message
        self.retrieved, self.retrieval = [], None
        if self.rag_mode == "on":
            try:
                self.retrieved = self.kb.search(message, RETRIEVAL_K)
            except kb_client.KbError as e:
                raise NotReady(f"Your document's knowledge base is not available: {e}") from e
            self.retrieval = self.kb.last
        self.rounds: list[ToolRound] = []
        self.budget, self.counter = budget(), counter()

    def request(self):
        messages, trace = build(self.config, self.history, self.message,
                                retrieved=self.retrieved, tool_rounds=self.rounds,
                                budget=self.budget, counter=self.counter)
        if self.retrieval:
            trace.retrieval = {**self.retrieval, **(trace.retrieval or {})}
        trace.payload = lamb_client.make_payload(self.model, messages, stream=True)
        return trace


def start_chat(session, message: str, rag_mode: str):
    """Validate and assemble the first request; return the SSE generator.

    Raises NotReady or ContextOverflow before anything is streamed, so the
    route can answer with a plain HTTP error."""
    check_rate(*scope(session))
    turn = _Turn(session, message, rag_mode)
    trace = turn.request()
    turn_id = store.start_turn(*turn.scope, message, turn.rag_mode)
    return _chat_stream(turn, turn_id, trace)


def _content(text: str) -> str:
    return frames.sse({"choices": [{"index": 0, "delta": {"content": text}}]})


def _marker_prefix(text: str) -> int:
    """Length of the longest end of `text` that could begin CALL_MARKER."""
    for n in range(min(len(CALL_MARKER) - 1, len(text)), 0, -1):
        if CALL_MARKER.startswith(text[-n:]):
            return n
    return 0


def _relay(trace, tools_on: bool, shown: list[str]):
    """Forward one LAMB answer to the student; return (reply, call).

    With tools on, text that may be the start of a call is held back. Once a
    complete call is read the stream is closed (the model was told to stop
    there) and the reply is cut right after the call. An unfinished call is
    shown as text: the format was not followed (`malformed`)."""
    reply, held = "", ""
    stream = lamb_client.stream(trace.payload)
    try:
        for delta in stream:
            trace.model = delta.model or trace.model
            reply += delta.content
            if not tools_on:
                shown.append(delta.content)
                yield _content(delta.content)
                continue
            held += delta.content
            at = held.find(CALL_MARKER)
            if at >= 0:
                if at:
                    shown.append(held[:at])
                    yield _content(held[:at])
                    held = held[at:]
                call = protocol.find_call(held)
                if call is not None:
                    return reply[:len(reply) - len(held)] + held[:call.end], call
                continue
            keep = _marker_prefix(held)
            if len(held) > keep:
                shown.append(held[:len(held) - keep])
                yield _content(held[:len(held) - keep])
                held = held[len(held) - keep:]
    finally:
        stream.close()
    if held and (call := protocol.find_call(held, at_end=True)) is not None:
        return reply[:len(reply) - len(held)] + held[:call.end], call
    if held:
        shown.append(held)
        yield _content(held)
        if CALL_MARKER in held:
            yield frames.sse({"type": "tool_event", "data": {"type": "tool_malformed"}})
    return reply, None


def _chat_stream(turn: _Turn, turn_id: int, trace):
    uid, rlid, guid = turn.scope
    shown: list[str] = []
    finished = False
    rounds_log = []
    try:
        for round_no in range(MAX_TOOL_ROUNDS + 1):
            if round_no:
                trace = turn.request()
                if shown and not shown[-1].endswith("\n"):
                    # Keep the text before the call apart from what follows.
                    shown.append("\n\n")
                    yield _content("\n\n")
            frame = frames.observability(trace, "request", turn.rag_mode)
            frame["data"]["round"] = round_no
            yield frames.sse(frame)
            tools_on = turn.tools_on and round_no < MAX_TOOL_ROUNDS
            reply, call = yield from _relay(trace, tools_on, shown)
            if call is None:
                break
            yield frames.sse({"type": "tool_event", "data": {
                "type": "tool", "name": call.name, "args": call.arguments, "round": round_no + 1}})
            ok, result = run_tool(call.name, call.arguments, turn.kb)
            yield frames.sse({"type": "tool_event", "data": {
                "type": "tool_done", "name": call.name, "success": ok, "result": result,
                "round": round_no + 1}})
            turn.rounds.append(ToolRound(reply, protocol.format_result(call, result, ok)))
            rounds_log.append({"name": call.name, "args": call.arguments, "ok": ok,
                               "result": result, "call_text": reply})
        answer = "".join(shown)
        data = {**trace.to_dict(), "tool_rounds": rounds_log}
        sent = [{"name": log["name"], "call": r.call, "result": r.result}
                for log, r in zip(rounds_log, turn.rounds)]
        store.finish_turn(turn_id, answer, trace.model, traces.save(uid, rlid, guid, "chat", data),
                          sent, last_reply=reply)
        finished = True
        yield frames.sse(frames.observability(trace, "result", turn.rag_mode))
        yield "data: [DONE]\n\n"
    except LambError as e:
        logger.warning("chat turn %s failed: %s (%s)", turn_id, e.kind, e.detail[:300])
        data = {**trace.to_dict(), "tool_rounds": rounds_log,
                "error": {**e.to_dict(), "detail": e.detail}}
        store.fail_turn(turn_id, e.kind, traces.save(uid, rlid, guid, "chat", data))
        finished = True
        yield frames.sse(frames.error(e.message, e.kind, e.hint))
    except ContextOverflow as e:
        # A tool round made the request too long to send.
        store.fail_turn(turn_id, "context_overflow",
                        traces.save(uid, rlid, guid, "chat", {**trace.to_dict(), "tool_rounds": rounds_log}))
        finished = True
        yield frames.sse(frames.error(
            "The tool results made the request longer than the model can read.",
            "context_overflow", f"About {e.needed} tokens; the limit is {e.limit}."))
    finally:
        if not finished:
            # The student pressed Stop (the client went away) or the server
            # failed mid-answer: keep the turn, but out of the history.
            store.fail_turn(turn_id, "aborted",
                            traces.save(uid, rlid, guid, "chat", trace.to_dict()))


# --- step 6 --------------------------------------------------------------------------

def activity_rubric(resource_link_id: str, consumer_guid: str) -> dict:
    """The teacher's rubric, or the default one when none was saved."""
    saved = store.get_settings(resource_link_id, consumer_guid)["rubric"]
    return evaluation.validate_rubric(saved or evaluation.DEFAULT_RUBRIC)


def formative_feedback(session) -> dict:
    """Run the rubric on the student's work and store the result (never in
    the gradebook). Returns what FormativeFeedback.svelte renders."""
    uid, rlid, guid = scope(session)
    rubric = activity_rubric(rlid, guid)
    result = evaluation.evaluate(uid, rlid, guid, rubric, engine_model())
    trace_id = traces.save(uid, rlid, guid, "feedback", result.pop("trace"))
    content = {"configured": True, **{k: v for k, v in result.items() if k != "model"}}
    store.add_feedback(uid, rlid, guid, rubric, content, result.get("model"), trace_id)
    return {**content, "model": result.get("model")}
