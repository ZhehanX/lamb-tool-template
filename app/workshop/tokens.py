"""Token estimates for the context budget (plan T14).

LAMB reports no usage for Ollama (`usage` is -1), and the engine model's own
tokenizer is not available to the tool, so every count here is an ESTIMATE:
tiktoken's o200k_base, scaled by a factor calibrated against the dev model
with scripts/calibrate_tokens.py. The panel says so, and names the tokenizer.

Digits need separate handling. o200k_base groups up to three digits into one
token, while Qwen's tokenizer gives every digit its own token, so a table of
numbers costs about 1.5x the o200k count on qwen3.5 against about 1.1x for
prose. With `split_digits`, digits are counted one per token and only the
rest of the text is scaled by the factor.

If tiktoken (or its encoding file, fetched on first use and cached under
TIKTOKEN_CACHE_DIR) is unavailable, a conservative characters-per-token rule
is used instead, and `TokenCounter.name` says that too."""

import math
import re
from functools import lru_cache

# Fallback when tiktoken cannot load: few characters per token over-counts,
# which only makes the budget stricter.
_FALLBACK_CHARS_PER_TOKEN = 2.5

_DIGIT_RUN = re.compile(r"\d+")


@lru_cache(maxsize=1)
def _o200k():
    try:
        import tiktoken
        return tiktoken.get_encoding("o200k_base")
    except Exception:  # missing package, or no network for the encoding file
        return None


def raw_o200k_count(text: str) -> int:
    """Unscaled o200k_base count (the fallback rule if tiktoken is missing)."""
    enc = _o200k()
    if enc is None:
        return math.ceil(len(text) / _FALLBACK_CHARS_PER_TOKEN)
    return len(enc.encode(text, disallowed_special=()))


class TokenCounter:
    """Estimate how many tokens the engine model will see.

    factor           — engine tokens per o200k token for non-digit text.
    split_digits     — the engine tokenizer gives each digit its own token.
    message_overhead — chat-template tokens added around each message.
    prompt_overhead  — chat-template tokens added once per request.

    The defaults are the qwen3.5:9b calibration of 2026-10-22."""

    def __init__(self, factor: float = 1.15, split_digits: bool = True,
                 message_overhead: int = 5, prompt_overhead: int = 16):
        self.factor = factor
        self.split_digits = split_digits
        self.message_overhead = message_overhead
        self.prompt_overhead = prompt_overhead

    @property
    def name(self) -> str:
        base = "o200k_base" if _o200k() is not None else f"chars/{_FALLBACK_CHARS_PER_TOKEN}"
        digits = ", 1 token per digit" if self.split_digits else ""
        return f"{base} x {self.factor:g}{digits}"

    def count(self, text: str) -> int:
        if not text:
            return 0
        raw = raw_o200k_count(text)
        if not self.split_digits or _o200k() is None:
            return math.ceil(raw * self.factor)
        runs = [len(r) for r in _DIGIT_RUN.findall(text)]
        # o200k pre-splits numbers into groups of at most three digits.
        o200k_digit_tokens = sum(math.ceil(n / 3) for n in runs)
        return math.ceil(max(raw - o200k_digit_tokens, 0) * self.factor) + sum(runs)

    def count_messages(self, messages: list[dict]) -> int:
        return self.prompt_overhead + sum(
            self.count(m["content"]) + self.message_overhead for m in messages)
