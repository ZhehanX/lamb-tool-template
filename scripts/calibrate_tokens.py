"""Calibrate the tool's token estimate against the dev model's real tokenizer.

Development only: the tool itself never talks to Ollama. It estimates tokens
with tiktoken's o200k_base (app/workshop/tokens.py), which counts differently
from Qwen's tokenizer, and qwen3.5 rejects an over-long prompt outright. This
script sends sample texts straight to Ollama the way LAMB does (model,
messages, stream only) and compares `prompt_eval_count` with the estimate.

Ollama reuses the KV cache for a repeated prompt prefix, so every prompt
starts with a fresh nonce; otherwise prompt_eval_count would only count the
uncached tail.

Usage:
  python scripts/calibrate_tokens.py FILE [FILE ...]
      [--model qwen3.5:9b] [--ollama http://localhost:11434] [--chars 12000]

Each FILE is a text sample (.md/.txt, or a .json whose string values are
joined, e.g. a locale file). Prints, per sample, the real count, the o200k
count and their ratio, and the ratio for the non-digit part when digits are
counted one per token (TokenCounter's split_digits). Then the per-message
overhead and a suggested factor (the largest non-digit ratio, rounded up).
"""

import argparse
import json
import math
import pathlib
import re
import sys
import uuid

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.workshop.tokens import raw_o200k_count  # noqa: E402


def load_text(path: pathlib.Path) -> str:
    raw = path.read_text(encoding="utf-8")
    if path.suffix != ".json":
        return raw
    values = []

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            values.append(node)

    walk(json.loads(raw))
    return "\n".join(values)


def prompt_eval_count(client, model, messages) -> int:
    # num_predict only stops the reply early (a thinking model would otherwise
    # think at length about each sample); the prompt is tokenized the same.
    r = client.post("/api/chat", json={"model": model, "messages": messages, "stream": False,
                                       "options": {"num_predict": 1}})
    r.raise_for_status()
    return r.json()["prompt_eval_count"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", type=pathlib.Path)
    ap.add_argument("--model", default="qwen3.5:9b")
    ap.add_argument("--ollama", default="http://localhost:11434")
    ap.add_argument("--chars", type=int, default=12000, help="characters taken from each sample")
    args = ap.parse_args()

    with httpx.Client(base_url=args.ollama, timeout=600) as c:
        # Fixed cost of a one-message prompt: chat template tokens + nonce.
        def nonce():
            return f"[{uuid.uuid4().hex}]\n"

        n = nonce()
        base = prompt_eval_count(c, args.model, [{"role": "user", "content": n}])
        base_overhead = base - raw_o200k_count(n)

        ratios = []
        print(f"{'sample':40} {'real':>7} {'o200k':>7} {'ratio':>6} {'non-digit':>9}")
        for path in args.files:
            text = load_text(path)[: args.chars]
            n = nonce()
            real = prompt_eval_count(c, args.model, [{"role": "user", "content": n + text}])
            real_content = real - base_overhead - raw_o200k_count(n)
            est = raw_o200k_count(text)
            runs = [len(r) for r in re.findall(r"\d+", text)]
            non_digit = (real_content - sum(runs)) / (est - sum(math.ceil(n / 3) for n in runs))
            ratios.append(non_digit)
            print(f"{path.name[:40]:40} {real_content:7d} {est:7d} {real_content / est:6.3f} {non_digit:9.3f}")

        # Extra cost of each additional message in the conversation.
        msgs = [{"role": "user", "content": nonce()}]
        one = prompt_eval_count(c, args.model, msgs)
        msgs += [{"role": "assistant", "content": "ok"}, {"role": "user", "content": "ok"},
                 {"role": "assistant", "content": "ok"}, {"role": "user", "content": "ok"}]
        five = prompt_eval_count(c, args.model, msgs)
        per_message = (five - one - 4 * raw_o200k_count("ok")) / 4

    factor = math.ceil(max(ratios) * 20) / 20
    print(f"\nbase overhead (1 message): {base_overhead} tokens")
    print(f"per extra message: {per_message:.1f} tokens")
    print(f"non-digit ratio range: {min(ratios):.3f} - {max(ratios):.3f}; suggested factor: {factor}")


if __name__ == "__main__":
    main()
