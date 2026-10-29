"""Context assembly (plan T3): the tool builds every message the engine sees.

LAMB is a transparent engine (T1, checked by tests/contract), so the messages
built here are exactly what the model receives. Alongside them the assembler
returns a trace that splits every message into segments tagged with where
the text came from. The panel renders the trace, and the invariant

    "".join(s.text for s in segments if s.message == i) == messages[i]["content"]

holds for every message, so the panel cannot show anything that was not sent
or hide anything that was. `scaffold` segments are the glue the tool itself
adds (headings, separators, truncation notes); they are shown too.

Layout:
  system     instructions (step 1), the whole document (step 2, T12; the
             caller leaves it out when step 3 searches it instead, T15) and
             the tool instructions (step 4); omitted when all are empty
  history    earlier exchanges, unchanged: the student's message, the tool
             rounds of that turn (T16), then the reply
  user       excerpts of the document (step 3), if any, then the student's message
  tools      per tool round: the model's reply with the call, then the result

The context budget (T14) is enforced here: first the results of earlier tool
rounds are replaced by a short note, oldest first (T16), then the document is
cut, then the oldest exchanges are dropped; if the request still does not
fit, ContextOverflow is raised. Every cut is recorded in the trace."""

from dataclasses import asdict, dataclass, field

from .ports import Chunk
from .tokens import TokenCounter

SOURCES = ("instructions", "document", "retrieval", "tools", "history", "user", "scaffold")

# Scaffold text, in English like the rest of the prompt plumbing. Kept in one
# place so the panel can explain each piece.
LABELS = {
    "block_separator": "\n\n",
    "document": "## Document: {name}\n\n",
    "document_truncated": "\n\n[Document truncated: the first {kept} of {total} characters are included.]",
    "retrieval": "Excerpts from the document:\n\n",
    "excerpt": "[{rank}] {source}\n",
    "excerpt_separator": "\n\n",
    "question": "\n\nQuestion:\n",
    "result_elided": '<tool_result name="{name}">[Earlier result left out to make room.]</tool_result>',
}


@dataclass
class Document:
    name: str
    text: str


@dataclass
class AssistantConfig:
    """The student's assistant as the wizard has built it so far."""
    instructions: str = ""
    document: Document | None = None
    tools_text: str = ""  # ToolProtocol.describe(...) for the enabled tools


@dataclass
class ToolRound:
    """One round of the text tool protocol: the model's reply containing the
    call, and the message that hands the result back."""
    call: str
    result: str


@dataclass
class Budget:
    """How much the engine model can read (T14).

    LAMB sends no num_ctx and reports no usage, so the limit is configured
    (32768 = OLLAMA_CONTEXT_LENGTH in dev), a share is kept free because the
    count is an estimate, and room is left for the answer, thinking included.

    `input_cap` is the student's step-5 experiment (T18): it caps the input
    directly, so the room for the answer stays what the real model needs, and
    it can only lower the limit."""
    context_tokens: int = 32768
    reserve_output: int = 3072
    margin: float = 0.15
    input_cap: int | None = None

    @property
    def input_limit(self) -> int:
        limit = int(self.context_tokens * (1 - self.margin)) - self.reserve_output
        return min(limit, self.input_cap) if self.input_cap else limit


@dataclass
class Segment:
    message: int
    role: str
    source: str
    text: str
    tokens: int
    meta: dict = field(default_factory=dict)


@dataclass
class Trace:
    messages: list[dict]
    segments: list[Segment]
    tokenizer: str
    tokens_estimated: int
    budget: dict
    retrieval: dict | None = None
    payload: dict | None = None  # set by the caller: exactly what goes to LAMB
    model: str | None = None     # set from LAMB's answer: the model that replied

    def to_dict(self) -> dict:
        return asdict(self)


class ContextOverflow(Exception):
    """The request does not fit the budget even without the document and the
    earlier exchanges."""

    def __init__(self, needed: int, limit: int):
        super().__init__(f"the request needs about {needed} tokens; the limit is {limit}")
        self.needed = needed
        self.limit = limit


class _Builder:
    def __init__(self, counter: TokenCounter):
        self.counter = counter
        self.messages: list[dict] = []
        self.segments: list[Segment] = []

    def message(self, role: str, parts: list[tuple[str, str, dict]]) -> None:
        parts = [p for p in parts if p[1]]
        if not parts:
            return
        index = len(self.messages)
        self.messages.append({"role": role, "content": "".join(text for _, text, _ in parts)})
        for source, text, meta in parts:
            self.segments.append(Segment(index, role, source, text, self.counter.count(text), meta))


def _excerpts(chunks: list[Chunk]) -> list[tuple[str, str, dict]]:
    parts = [("scaffold", LABELS["retrieval"], {})]
    for i, c in enumerate(chunks):
        if i:
            parts.append(("scaffold", LABELS["excerpt_separator"], {}))
        parts.append(("scaffold", LABELS["excerpt"].format(rank=c.rank, source=c.source), {}))
        parts.append(("retrieval", c.text, {"rank": c.rank, "source": c.source, "score": c.score}))
    return parts


def _cut_to_tokens(text: str, max_tokens: int, counter: TokenCounter) -> str:
    """Longest prefix of `text` estimated at <= max_tokens, ending on
    whitespace when that costs little."""
    if max_tokens <= 0:
        return ""
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if counter.count(text[:mid]) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    cut = text[:lo]
    space = max(cut.rfind("\n"), cut.rfind(" "))
    if lo < len(text) and space > lo - 200:
        cut = cut[:space]
    return cut


def _elide_oldest_result(history: list[dict]) -> list[dict] | None:
    """History with its oldest tool result replaced by a short note, or None
    if no result is left that the note would shorten."""
    for i, m in enumerate(history):
        if m.get("tool_round") == "result" and not m.get("elided"):
            note = LABELS["result_elided"].format(name=m.get("tool", ""))
            if len(note) < len(m["content"]):
                return history[:i] + [{**m, "content": note, "elided": True}] + history[i + 1:]
    return None


def _drop_oldest(history: list[dict]) -> list[dict]:
    """History without its oldest exchange: everything up to the student's
    next message, the tool rounds in between included."""
    for i in range(1, len(history)):
        if history[i]["role"] == "user" and not history[i].get("tool_round"):
            return history[i:]
    return []


def build(config: AssistantConfig, history: list[dict], user_input: str,
          retrieved: list[Chunk] = (), tool_rounds: list[ToolRound] = (),
          budget: Budget | None = None, counter: TokenCounter | None = None):
    """Return (messages, trace) for one request to the engine.

    history     earlier {role, content} messages, oldest first; those of a
                tool round also carry `tool_round` ("call"/"result") and `tool`
    retrieved   all retrieval candidates; only the selected ones are sent
    tool_rounds rounds of the text tool protocol already run for this turn"""
    budget = budget or Budget()
    counter = counter or TokenCounter()
    selected = [c for c in retrieved if c.selected]
    doc = config.document
    limit = budget.input_limit

    def assemble(doc_text: str | None, note: str, kept_history: list[dict]) -> _Builder:
        b = _Builder(counter)
        blocks = [[("instructions", config.instructions, {})]]
        if doc_text is not None:
            blocks.append([("scaffold", LABELS["document"].format(name=doc.name), {}),
                           ("document", doc_text, {"name": doc.name}),
                           ("scaffold", note, {"truncation": True})])
        blocks.append([("tools", config.tools_text, {})])
        system = []
        for block in (blk for blk in blocks if any(text for _, text, _ in blk)):
            if system:
                system.append(("scaffold", LABELS["block_separator"], {}))
            system.extend(block)
        b.message("system", system)
        for m in kept_history:
            if m.get("elided"):
                part = ("scaffold", m["content"], {"elided": True})
            else:
                part = ("history", m["content"],
                        {"tool_round": m["tool_round"]} if m.get("tool_round") else {})
            b.message(m["role"], [part])
        user = _excerpts(selected) + [("scaffold", LABELS["question"], {})] if selected else []
        b.message("user", user + [("user", user_input, {})])
        for r in tool_rounds:
            b.message("assistant", [("tools", r.call, {"tool_round": "call"})])
            b.message("user", [("tools", r.result, {"tool_round": "result"})])
        return b

    def fit(kept_history: list[dict], cut_document: bool = True):
        """The request with the document cut just enough, or None if it does
        not fit even with nothing of the document left (or, without
        cut_document, with all of it)."""
        b = assemble(doc.text if doc else None, "", kept_history)
        total = counter.count_messages(b.messages)
        if total <= limit:
            return b, total, doc.text if doc else None
        if doc is None or not cut_document:
            return None

        def note(kept: int) -> str:
            return LABELS["document_truncated"].format(kept=kept, total=len(doc.text))

        rest = counter.count_messages(assemble("", note(len(doc.text)), kept_history).messages)
        room = limit - rest
        # Token counts are not exactly additive across segment boundaries;
        # shrink until the whole request is within the limit.
        while room >= 0:
            kept = _cut_to_tokens(doc.text, room, counter)
            b = assemble(kept, note(len(kept)), kept_history)
            total = counter.count_messages(b.messages)
            if total <= limit:
                return b, total, kept
            room -= total - limit
        return None

    # Earlier tool results give way first, then the document, then the
    # oldest exchanges (see the module docstring).
    kept_history, elided, dropped = list(history), 0, 0
    result = fit(kept_history, cut_document=False)
    while result is None and (shorter := _elide_oldest_result(kept_history)) is not None:
        kept_history, elided = shorter, elided + 1
        result = fit(kept_history, cut_document=False)
    while result is None and (result := fit(kept_history)) is None:
        if not kept_history:
            b = assemble(None, "", [])
            raise ContextOverflow(counter.count_messages(b.messages), limit)
        shorter = _drop_oldest(kept_history)
        dropped += len(kept_history) - len(shorter)
        kept_history = shorter
    b, total, kept_text = result

    doc_info = None
    if doc is not None:
        doc_info = {"name": doc.name, "total_chars": len(doc.text), "kept_chars": len(kept_text),
                    "total_tokens": counter.count(doc.text), "kept_tokens": counter.count(kept_text),
                    "truncated": len(kept_text) < len(doc.text)}
    trace = Trace(
        messages=b.messages,
        segments=b.segments,
        tokenizer=counter.name,
        tokens_estimated=total,
        budget={**asdict(budget), "input_limit": limit, "document": doc_info,
                "history_dropped": dropped, "tool_results_elided": elided},
        # Every candidate, not only the selected ones, so the panel can show
        # where the top-k cut fell. The retriever's caller adds query/timing.
        retrieval={"candidates": [asdict(c) for c in retrieved]} if retrieved else None,
    )
    return b.messages, trace
