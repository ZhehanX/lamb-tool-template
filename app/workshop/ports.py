"""The three replaceable interfaces of the workshop (plan T5).

Upper layers (assembler, chat flow, panel) depend only on these, so an
implementation can be swapped without touching them: a fake Retriever in
unit tests and KbServerRetriever (own lamb-kb-server, Phase 8) in the tool;
TextToolProtocol because LAMB's facade forwards no `tools`; LocalTrace in the
tool's SQLite."""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Chunk:
    """One retrieval candidate. `selected` marks the top-k that go into the
    prompt; the others are kept so the panel can show where the cut fell."""
    text: str
    score: float
    source: str
    rank: int
    selected: bool
    meta: dict = field(default_factory=dict)


class Retriever(Protocol):
    def search(self, query: str, k: int) -> list[Chunk]:
        """All candidates, best first, with the first k marked selected."""
        ...


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    argument: str  # what goes inside the call, in words: "an arithmetic expression"


@dataclass(frozen=True)
class ToolCall:
    """A tool call as the model wrote it in its reply."""
    name: str
    arguments: str
    raw: str
    start: int
    end: int


class ToolProtocol(Protocol):
    def describe(self, tools: list[ToolSpec]) -> str:
        """Instructions for the system message: the tools and the exact format."""
        ...

    def find_call(self, text: str, at_end: bool = False) -> ToolCall | None:
        """The first well-formed call in a reply, or None. `at_end` says the
        reply is complete, which some formats need to recognise a call."""
        ...

    def format_result(self, call: ToolCall, result: str, ok: bool) -> str:
        """The message content that hands a result back to the model."""
        ...


class TraceSource(Protocol):
    def save(self, user_id: int, resource_link_id: str, consumer_guid: str,
             kind: str, data: dict) -> int:
        ...

    def get(self, trace_id: int) -> dict | None:
        ...
