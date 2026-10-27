"""Phase 6: how well the engine model follows the text tool protocol.

Runs ten questions that need the calculator through the real chat pipeline
(assembler -> LAMB -> tool loop), each in a fresh conversation, and reports
per question whether the model
  - called:  wrote a well-formed calculator call,
  - correct: called it with an expression whose value is the right answer,
  - answer:  put that number in its final answer.
Plan Phase 6 DoD: correct >= 9/10 on qwen3.5:9b.

Development only. Uses a throwaway database, so the dev tool's data is not
touched; LAMB must be running.

Usage:
  python scripts/tool_compliance.py [--style tag|qwen] [--engine lamb_assistant.2]
      [--lamb http://localhost:9099] [--key-file .secrets/lamb_api_key.txt] [--json out.json]
"""

import argparse
import json
import os
import pathlib
import re
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
os.environ.update({"ADMIN_USERNAME": "x", "ADMIN_PASSWORD": "x", "PUBLIC_BASE_URL": "http://x",
                   "TOOL_DB_PATH": os.path.join(tempfile.mkdtemp(prefix="compliance-"), "tool.db")})
sys.path.insert(0, str(ROOT))

from app import db, settings_store  # noqa: E402
from app.workshop import service, store  # noqa: E402
from app.workshop.text_tools import TextToolProtocol  # noqa: E402

INSTRUCTIONS = "Respon breument, en la llengua de la pregunta. Fes servir la calculadora per als càlculs."

QUESTIONS = [
    ("Quant fa 17 × 23?", 391),
    ("¿Cuánto es el 15 % de 2340?", 351),
    ("What is 1234 multiplied by 5678?", 7006652),
    ("Si compro 7 llibres a 12,35 € cadascun, quant pago en total?", 86.45),
    ("Calcula (125 + 375) / 4.", 125),
    ("Un tren recorre 342 km en 4,5 horas. ¿A qué velocidad media va, en km/h?", 76),
    ("What is 2 to the power of 20?", 1048576),
    ("Tinc 1500 € a un interès compost del 3 % anual. Quant tindré al cap de 4 anys?", 1688.26),
    ("¿Cuántos segundos hay en 3 días?", 259200),
    ("What is 987654 divided by 321? Give two decimals.", 3076.80),
]


def close(value: str, expected: float) -> bool:
    try:
        v = float(value)
    except ValueError:
        return False
    return abs(v - expected) <= max(0.006, abs(expected) * 1e-6)


def in_answer(text: str, expected: float) -> bool:
    """The expected number written in any usual way (1,688.26 / 1.688,26 / 1688.26)."""
    for raw in re.findall(r"\d[\d.,   ]*\d|\d", text):
        digits = re.sub(r"[   ]", "", raw)
        candidates = {digits.replace(",", ""), digits.replace(".", "").replace(",", ".")}
        if any(close(c, expected) for c in candidates):
            return True
    return False


def run(question: str, expected: float, n: int) -> dict:
    uid = db.upsert_user(f"student-{n}", "compliance", f"s{n}", "Student", "")
    db.save_activity("link", "compliance", "c", "c", "t", "unused", "", False, 3, uid)
    session = {"user_id": uid, "resource_link_id": "link", "consumer_guid": "compliance"}
    store.save_assistant(uid, "link", "compliance", instructions=INSTRUCTIONS, tools=["calculator"])
    started = time.time()
    events, answer, error = [], "", None
    for raw in service.start_chat(session, question, "off"):
        data = raw[len("data: "):].strip()
        if data == "[DONE]":
            break
        frame = json.loads(data)
        if frame.get("type") == "tool_event":
            events.append(frame["data"])
        elif frame.get("type") == "error":
            error = frame["data"]["message"]
        elif "choices" in frame:
            answer += frame["choices"][0]["delta"]["content"]
    calls = [e for e in events if e["type"] == "tool"]
    done = [e for e in events if e["type"] == "tool_done"]
    return {
        "question": question,
        "expected": expected,
        "called": bool(calls),
        "correct": any(e["success"] and close(e["result"], expected) for e in done),
        "answer_ok": in_answer(answer, expected),
        "malformed": any(e["type"] == "tool_malformed" for e in events),
        "calls": [(c["args"], d["result"]) for c, d in zip(calls, done)],
        "answer": answer.strip(),
        "error": error,
        "seconds": round(time.time() - started, 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--style", choices=["tag", "qwen"], default="tag")
    ap.add_argument("--engine", default="lamb_assistant.2")
    ap.add_argument("--lamb", default="http://localhost:9099")
    ap.add_argument("--key-file", default=str(ROOT / ".secrets" / "lamb_api_key.txt"))
    ap.add_argument("--json")
    args = ap.parse_args()

    db.init_db()
    settings_store.set_value("lamb_api_base", args.lamb)
    settings_store.set_value("lamb_api_key", pathlib.Path(args.key_file).read_text().strip())
    settings_store.set_value("engine_model", args.engine)
    service.protocol = TextToolProtocol(args.style)

    results = []
    for n, (question, expected) in enumerate(QUESTIONS, 1):
        r = run(question, expected, n)
        results.append(r)
        flags = "".join("Y" if r[k] else "-" for k in ("called", "correct", "answer_ok"))
        print(f"{n:2d} [{flags}] {r['seconds']:5.1f}s calls={r['calls']} answer={r['answer'][:70]!r}"
              + (f" ERROR={r['error']}" if r["error"] else "") + (" MALFORMED" if r["malformed"] else ""))
    total = len(results)
    summary = {k: sum(r[k] for r in results) for k in ("called", "correct", "answer_ok", "malformed")}
    print(f"\nstyle={args.style}  called {summary['called']}/{total}  correct {summary['correct']}/{total}"
          f"  answer {summary['answer_ok']}/{total}  malformed {summary['malformed']}"
          f"  mean {sum(r['seconds'] for r in results) / total:.1f}s per question")
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps({"style": args.style, "summary": summary,
                                                       "results": results}, ensure_ascii=False, indent=2),
                                         encoding="utf-8")


if __name__ == "__main__":
    main()
