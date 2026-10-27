"""Step 6: rubric-based formative feedback (plan Phase 7).

Ported from the old branch's `lamb/modules/workshop/evaluation.py`. The JSON
handling (repairing a reply cut off mid-object, aligning the model's criteria
with the rubric's, dropping invented ones) is kept as it was; what changed:

- the rubric is the tool's own, edited by the teacher in the tool, in a small
  JSON shape (RUBRIC_SHAPE) instead of LAMB evaluaitor's rubric table;
- the transcript is read from the tool's own data (assistant, turns,
  reflection, step-3 probes);
- the call goes to the engine assistant through LAMB (non-streamed), like
  every other request, and is traced like a chat turn;
- the total is computed here from the rubric's weights when the criteria have
  scores, instead of trusting the model's arithmetic.

Feedback is a suggestion for the student. It is never written to the
gradebook; the teacher decides the grade."""

import json
import re
from typing import Any

from .. import lamb_client
from . import store

RUBRIC_SHAPE = """{
  "title": "...",
  "criteria": [
    {"name": "...", "weight": 25, "description": "...",
     "levels": [{"label": "...", "score": 4, "description": "..."}, ...]},
    ...
  ]
}"""

DEFAULT_RUBRIC = {
    "title": "AI Workshop — formative rubric",
    "criteria": [
        {"name": "Instructions", "weight": 25,
         "description": "The instructions say what the assistant is for, how it should answer and what it should not do.",
         "levels": [
             {"label": "Excellent", "score": 4, "description": "Clear purpose, tone and limits, tested and revised."},
             {"label": "Good", "score": 3, "description": "Clear purpose and tone; limits partly missing."},
             {"label": "Basic", "score": 2, "description": "A general purpose only."},
             {"label": "Missing", "score": 1, "description": "No usable instructions."}]},
        {"name": "Sources", "weight": 25,
         "description": "The document is used on purpose, whole or as searched excerpts, and the student can tell when the answer comes from it.",
         "levels": [
             {"label": "Excellent", "score": 4, "description": "Compared answers with the whole document and with its excerpts and explained the difference."},
             {"label": "Good", "score": 3, "description": "Used the document and noticed its effect."},
             {"label": "Basic", "score": 2, "description": "Attached a document without checking its effect."},
             {"label": "Missing", "score": 1, "description": "No document."}]},
        {"name": "Tools", "weight": 20,
         "description": "Tools are chosen for a reason and the student understands that a tool call is text the model writes.",
         "levels": [
             {"label": "Excellent", "score": 4, "description": "Triggered a tool on purpose and read the call and the result in the panel."},
             {"label": "Good", "score": 3, "description": "Chose a sensible tool and saw it used."},
             {"label": "Basic", "score": 2, "description": "Selected a tool without testing it."},
             {"label": "Missing", "score": 1, "description": "No reasoning about tools."}]},
        {"name": "Reflection", "weight": 30,
         "description": "The reflection explains what the assistant actually received and what the student would change.",
         "levels": [
             {"label": "Excellent", "score": 4, "description": "Refers to concrete evidence from the panel and proposes specific changes."},
             {"label": "Good", "score": 3, "description": "Describes what was received and some changes."},
             {"label": "Basic", "score": 2, "description": "General statements only."},
             {"label": "Missing", "score": 1, "description": "No reflection."}]},
    ],
}

DESIGN_QUESTIONS = (
    ("q1", "What did you ask your assistant to do, and why?"),
    ("q2", "What did the assistant actually receive?"),
    ("q3", "What would you change in your design after testing it?"),
)

# Prompt budget for the transcript, in characters. The full transcript is
# shown to the teacher; this only bounds what is sent to the model.
MAX_TRANSCRIPT_CHARS = 8000
MAX_COMPARISON_TURNS = 6


class RubricError(ValueError):
    pass


# --- rubric --------------------------------------------------------------------

def validate_rubric(rubric: Any) -> dict:
    """The rubric in RUBRIC_SHAPE, cleaned; RubricError says what is wrong."""
    if not isinstance(rubric, dict):
        raise RubricError("The rubric must be a JSON object.")
    criteria = rubric.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise RubricError("The rubric needs a non-empty \"criteria\" list.")
    clean = []
    names = set()
    for i, c in enumerate(criteria, 1):
        if not isinstance(c, dict) or not str(c.get("name") or "").strip():
            raise RubricError(f"Criterion {i} needs a \"name\".")
        name = str(c["name"]).strip()
        if name.lower() in names:
            raise RubricError(f"Two criteria are called \"{name}\".")
        names.add(name.lower())
        weight = c.get("weight", 1)
        if not isinstance(weight, (int, float)) or weight < 0:
            raise RubricError(f"The weight of \"{name}\" must be a number >= 0.")
        levels = c.get("levels") or []
        if not isinstance(levels, list) or not levels:
            raise RubricError(f"\"{name}\" needs at least one level.")
        clean_levels = []
        for level in levels:
            if (not isinstance(level, dict) or not str(level.get("label") or "").strip()
                    or not isinstance(level.get("score"), (int, float))):
                raise RubricError(f"Every level of \"{name}\" needs a \"label\" and a numeric \"score\".")
            clean_levels.append({"label": str(level["label"]).strip(), "score": level["score"],
                                 "description": str(level.get("description") or "").strip()})
        clean.append({"name": name, "weight": weight,
                      "description": str(c.get("description") or "").strip(),
                      "levels": clean_levels})
    return {"title": str(rubric.get("title") or "").strip(), "criteria": clean}


def max_score(rubric: dict) -> float | None:
    scores = [lv["score"] for c in rubric["criteria"] for lv in c["levels"]]
    return max(scores) if scores else None


def rubric_instructions(rubric: dict) -> str:
    lines = [
        "You give formative feedback to a student who has just built an AI assistant in a "
        "guided workshop: they wrote its instructions, attached a document, could connect "
        "that document as a knowledge base (then, with the knowledge base ON, only the "
        "excerpts most similar to each message are sent; OFF, the whole document) and "
        "choose tools, tested it in a chat while watching exactly what the model received, "
        "and answered three reflection questions.",
        "Judge the student's work in the transcript against this rubric. Be specific and "
        "kind, point to evidence in the transcript, and suggest one next step per criterion. "
        "Write the feedback in the language the student used in the reflection.",
        "",
        f"RUBRIC: {rubric.get('title') or ''}",
    ]
    for c in rubric["criteria"]:
        lines.append(f"- Criterion \"{c['name']}\" (weight {c['weight']}): {c['description']}")
        for lv in c["levels"]:
            lines.append(f"    level \"{lv['label']}\" (score {lv['score']}): {lv['description']}")
    return "\n".join(lines)


# --- transcript ----------------------------------------------------------------

def build_transcript(user_id: int, resource_link_id: str, consumer_guid: str) -> str:
    """The student's work as text: build decisions, conversation (with the
    knowledge-base mode and tool rounds), step-3 probes and reflection."""
    scope = (user_id, resource_link_id, consumer_guid)
    row = store.get_assistant(*scope)
    lines = ["=== BUILD DECISIONS ==="]
    instructions = (row["instructions"] if row else "").strip()
    lines.append("Assistant instructions:\n" + (instructions or "(none provided)"))
    lines.append("Attached document: " + ((row["document_name"] if row else None) or "(none)"))
    lines.append("Document connected as a knowledge base: "
                 + ("yes" if row and row["retrieval_enabled"] else "no"))
    tools = json.loads(row["tools"] or "[]") if row else []
    lines.append("Selected tools: " + (", ".join(tools) or "(none)"))

    lines += ["", "=== CONVERSATION ==="]
    turns = store.get_turns(*scope)
    if not turns:
        lines.append("(no conversation)")
    conversation = None
    for t in turns:
        if t["conversation"] != conversation:
            if conversation is not None:
                lines.append("--- new conversation ---")
            conversation = t["conversation"]
        mode = f"[knowledge base {'ON' if t['rag_mode'] == 'on' else 'OFF'}, excerpts sent: {t['retrieved']}]"
        lines.append(f"Student {mode}: {t['user_content']}")
        for r in _tool_rounds(t["trace_id"]):
            lines.append(f"[tool call {r['name']}({r['args']}) -> {'ok' if r['ok'] else 'error'}: {r['result']}]")
        if t["status"] == "ok":
            lines.append(f"Assistant: {t['assistant_content']}")
        else:
            lines.append(f"(no answer: {t['error']})")

    probes = _probes(*scope)
    lines += ["", "=== KNOWLEDGE BASE TESTS (step 3) ==="]
    lines += [f"Test question {p['query']!r}: {len(p.get('results') or [])} excerpt(s) listed"
              for p in probes] or ["(none)"]

    lines += ["", "=== REFLECTION ==="]
    answers = (store.get_reflection(*scope) or {}).get("answers") or {}
    for key, question in DESIGN_QUESTIONS:
        lines.append(f"Q: {question}")
        lines.append(f"A: {(answers.get(key) or '').strip() or '(not answered)'}")
    return "\n".join(lines)


def _tool_rounds(trace_id) -> list:
    if not trace_id:
        return []
    data = store.LocalTrace().get(trace_id) or {}
    return data.get("tool_rounds") or []


def _probes(user_id, resource_link_id, consumer_guid) -> list:
    with store.get_conn() as conn:
        rows = conn.execute(
            """SELECT data FROM traces WHERE user_id = ? AND resource_link_id = ?
                 AND consumer_guid = ? AND kind = 'probe' ORDER BY id""",
            (user_id, resource_link_id, consumer_guid)).fetchall()
    return [json.loads(r["data"]) for r in rows]


def clamp_for_prompt(text: str, max_chars: int = MAX_TRANSCRIPT_CHARS) -> str:
    """Trim an over-long transcript, keeping both ends: the head has the build
    decisions, the tail the reflection, the parts the rubric grades most."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    marker = "\n\n[... conversation shortened for length ...]\n\n"
    head = int((max_chars - len(marker)) * 0.4)
    tail = max_chars - len(marker) - head
    return text[:head].rstrip() + marker + text[-tail:].lstrip()


# --- the model's answer (ported as is) ------------------------------------------

_JSON_OUTPUT_DIRECTIVE = (
    "\n\nIMPORTANT: Respond with ONLY one valid JSON object and nothing else. "
    "Do not use markdown, code fences, or prose. Use exactly these keys: "
    '{"criteria": [{"criterion": string, "level": string, "score": number, '
    '"feedback": string}], "overall_feedback": string}. '
    "criteria must contain one entry per rubric criterion. "
    "Keep every \"feedback\" to at most two short sentences and the whole JSON "
    "compact, with no line breaks inside string values."
)


def build_evaluation_directive(rubric: dict) -> str:
    listing = "\n".join(f"- \"{c['name']}\"" for c in rubric["criteria"])
    return (_JSON_OUTPUT_DIRECTIVE
            + f"\n\nEvaluate EXACTLY these {len(rubric['criteria'])} rubric criteria, "
              "one entry each, in this order:\n" + listing
            + "\nUse those names verbatim as the \"criterion\" value, and one of the "
              "criterion's level labels as \"level\". Do not add, rename, split, merge or "
              "repeat criteria.")


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped)
        stripped = re.sub(r"\s*```\s*$", "", stripped)
    return stripped.strip()


def _repair_truncated_json(text: str) -> str:
    """Close a JSON object the model stopped writing in the middle of; a cut
    string ends with an ellipsis so a reader can see it was clipped."""
    out, stack, in_string, escape = [], [], False, False
    for ch in text:
        out.append(ch)
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            stack.append(ch)
        elif ch in "]}" and stack:
            stack.pop()
    repaired = "".join(out)
    if escape:
        repaired = repaired[:-1]
    if in_string:
        repaired += '…"'
    repaired = re.sub(r",\s*$", "", repaired.rstrip())
    for opener in reversed(stack):
        repaired += "]" if opener == "[" else "}"
    return repaired


def _load_json_object(candidate: str) -> dict | None:
    attempts = [candidate, re.sub(r",(\s*[}\]])", r"\1", candidate)]
    repaired = _repair_truncated_json(candidate)
    attempts += [repaired, re.sub(r",(\s*[}\]])", r"\1", repaired)]
    for attempt in attempts:
        try:
            parsed = json.loads(attempt)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def parse_evaluation_json(raw: str) -> tuple[dict, str | None]:
    """(data, note): note says when the reply was cut off and repaired.
    Raises ValueError when no object can be recovered."""
    text = _strip_code_fences(raw or "")
    if not text:
        raise ValueError("Empty model response")
    start = text.find("{")
    candidate = text[start:] if start != -1 else text
    parsed = _load_json_object(candidate)
    if parsed is not None:
        balanced = _repair_truncated_json(candidate) == candidate.rstrip()
        return parsed, None if balanced else (
            "The model's response was cut off before the JSON was complete; "
            "the fields it did produce are shown.")
    end = text.rfind("}")
    if start != -1 and end > start and (parsed := _load_json_object(text[start:end + 1])):
        return parsed, None
    raise ValueError("Could not read the model's response as JSON.")


def _match_key(value: Any) -> str:
    text = str(value or "").lower().replace("&", " and ")
    return " ".join(re.sub(r"[^\w]+", " ", text).split())


def _first(data: dict, *keys: str):
    for key in keys:
        if data.get(key) not in (None, ""):
            return data[key]
    return None


def normalize_evaluation(data: dict, rubric: dict) -> dict:
    """Map the model's output onto the rubric: the rubric decides which
    criteria exist, their names and weights; invented criteria are dropped,
    repeats collapse, the order is the rubric's."""
    raw = _first(data, "criteria", "criterion_evaluations", "evaluations") or []
    if isinstance(raw, dict):
        raw = [{"criterion": k, **(v if isinstance(v, dict) else {"feedback": v})}
               for k, v in raw.items()]
    by_key = {_match_key(c["name"]): i for i, c in enumerate(rubric["criteria"])}
    found: dict[int, dict] = {}
    dropped = 0
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        name = _first(entry, "criterion", "name", "title")
        key = _match_key(name)
        pos = by_key.get(key)
        if pos is None and len(key) >= 4:
            pos = next((i for k, i in by_key.items() if len(k) >= 4 and (k in key or key in k)), None)
        if pos is None:
            dropped += 1
            continue
        if pos in found:
            continue
        found[pos] = entry

    criteria = []
    for pos, c in enumerate(rubric["criteria"]):
        entry = found.get(pos)
        if entry is None:
            continue
        level = _first(entry, "level", "selected_level")
        score = _first(entry, "score", "points")
        if score is None and level is not None:
            # The rubric owns the scale: a level label maps to its score.
            score = next((lv["score"] for lv in c["levels"]
                          if _match_key(lv["label"]) == _match_key(level)), None)
        feedback = _first(entry, "feedback", "justification", "comment")
        if level is None and score is None and not feedback:
            continue
        criteria.append({"criterion": c["name"], "weight": c["weight"], "level": level,
                         "score": _number(score), "feedback": feedback})

    scored = [c for c in criteria if c["score"] is not None]
    weights = sum(c["weight"] for c in scored)
    total = (round(sum(c["score"] * c["weight"] for c in scored) / weights, 2)
             if scored and weights else None)
    return {"criteria": criteria, "overall_feedback": _first(data, "overall_feedback", "overall", "summary"),
            "total_score": total, "max_score": max_score(rubric),
            "diagnostics": {"matched": len(criteria), "dropped": dropped,
                            "rubric_total": len(rubric["criteria"])}}


def _number(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# --- running it ------------------------------------------------------------------

def evaluate(user_id: int, resource_link_id: str, consumer_guid: str,
             rubric: dict, model: str) -> dict:
    """Run the rubric through the engine. Failures come back as status
    "failed" with a message, never as an exception: a submission must not
    fail because feedback could not be generated."""
    transcript = build_transcript(user_id, resource_link_id, consumer_guid)
    messages = [
        {"role": "system", "content": rubric_instructions(rubric) + build_evaluation_directive(rubric)},
        {"role": "user", "content": clamp_for_prompt(transcript)},
    ]
    payload = lamb_client.make_payload(model, messages, stream=False)
    trace = {"messages": messages, "payload": payload, "rubric": rubric}
    try:
        completion = lamb_client.complete(payload)
    except lamb_client.LambError as e:
        trace["error"] = {**e.to_dict(), "detail": e.detail}
        return {"status": "failed", "error_message": e.message, "hint": e.hint,
                "criteria": [], "trace": trace, "model": None}
    trace["model"] = completion.model
    trace["raw"] = completion.content
    try:
        data, note = parse_evaluation_json(completion.content)
    except ValueError as e:
        return {"status": "failed", "error_message": str(e), "criteria": [],
                "trace": trace, "model": completion.model}
    result = normalize_evaluation(data, rubric)
    diag = result["diagnostics"]
    notes = [note] if note else []
    if diag["rubric_total"] and diag["matched"] < diag["rubric_total"]:
        notes.append(f"{diag['matched']} of {diag['rubric_total']} rubric criteria could be graded; "
                     "the model did not score the rest.")
    if not diag["matched"]:
        return {**result, "status": "failed", "trace": trace, "model": completion.model,
                "error_message": "The model did not grade any criterion of this rubric. Try again."}
    return {**result, "status": "completed", "error_message": " ".join(notes) or None,
            "trace": trace, "model": completion.model}
