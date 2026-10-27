"""Instructor-facing surfaces: first-launch setup, the dashboard (roster +
grading), and grade passback to the LMS.

taller-ia: the setup no longer picks a LAMB assistant. Every activity uses
the workshop engine set on /admin (plan T13); the teacher sets the rubric
for formative feedback, what students are told about the model, and whether
students wait until the teacher has tried the workshop.

Grade policy in this template is deliberately simple and pluggable:
  - an optional auto-completion proposal: >= min_turns student turns -> 1.0
  - the instructor sets/overrides the final score before anything is sent
Replace propose_completion_score() with a rubric call, an assistant-as-
judge, or anything else — the approve-then-send flow stays the same."""

import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from .. import db, sessions, settings_store
from ..lti import outcomes
from ..workshop import evaluation, retention, store

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def propose_completion_score(user_turns: int, min_turns: int) -> float | None:
    if min_turns <= 0:
        return None
    return 1.0 if user_turns >= min_turns else round(user_turns / min_turns, 2)


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    session = sessions.require_instructor(request)
    rlid, guid = session["resource_link_id"], session["consumer_guid"]
    settings = store.get_settings(rlid, guid)
    default = json.dumps(evaluation.DEFAULT_RUBRIC, ensure_ascii=False, indent=2)
    return templates.TemplateResponse(request, "setup.html", {
        "session_token": session["token"],
        "activity": db.get_activity(rlid, guid),
        "settings": settings,
        # The engine is a deployment setting (plan T13), shown, not chosen.
        "engine": settings_store.get("engine_model"),
        "rubric_shape": evaluation.RUBRIC_SHAPE,
        "rubric_json": json.dumps(settings["rubric"], ensure_ascii=False, indent=2)
        if settings["rubric"] else default,
        "default_rubric_json": default,
        "error": None,
    })


@router.post("/setup")
async def setup_save(request: Request):
    session = sessions.require_instructor(request)
    form = await request.form()
    try:
        rubric = evaluation.validate_rubric(json.loads(form.get("rubric") or "{}"))
    except json.JSONDecodeError as e:
        return JSONResponse({"ok": False, "error": f"The rubric is not valid JSON: {e}"}, status_code=400)
    except evaluation.RubricError as e:
        return JSONResponse({"ok": False, "error": f"Rubric: {e}"}, status_code=400)
    rlid, guid = session["resource_link_id"], session["consumer_guid"]
    engine = settings_store.get("engine_model") or ""
    db.save_activity(
        resource_link_id=rlid,
        consumer_guid=guid,
        context_id=form.get("context_id", ""),
        context_title=form.get("context_title", ""),
        title=form.get("title") or "AI Workshop",
        # The template's column; the workshop always uses the /admin engine.
        assistant_model=engine,
        assistant_name="workshop engine",
        grading_enabled=form.get("grading_enabled") == "on",
        min_turns=int(form.get("min_turns") or 3),
        created_by=session["user_id"],
    )
    store.save_settings(rlid, guid, rubric=rubric, teacher_first=form.get("teacher_first") == "on",
                        consent_model_note=(form.get("consent_model_note") or "").strip())
    return JSONResponse({"ok": True, "redirect": f"/dashboard?session={session['token']}"})


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    session = sessions.require_instructor(request)
    activity = db.get_activity(session["resource_link_id"], session["consumer_guid"])
    rows = db.participants(session["resource_link_id"], session["consumer_guid"])
    min_turns = activity["min_turns"] if activity else 3
    people = []
    for r in rows:
        feedback = json.loads(r["feedback_json"]) if r["feedback_json"] else None
        people.append({
            **{k: r[k] for k in r.keys()},
            "proposed": propose_completion_score(r["user_turns"], min_turns),
            # A suggestion from the rubric, shown to the teacher; never sent.
            "suggested": (f"{feedback['total_score']:g} / {feedback['max_score']:g}"
                          if feedback and feedback.get("total_score") is not None
                          and feedback.get("max_score") else None),
        })
    return templates.TemplateResponse(request, "dashboard.html", {
        "session_token": session["token"],
        "activity": activity,
        "people": people,
    })


@router.get("/dashboard/student/{user_id}", response_class=HTMLResponse)
def student_work(request: Request, user_id: int):
    """One student's work as the formative feedback reads it, and the
    feedback itself. Only for students enrolled in this activity."""
    session = sessions.require_instructor(request)
    rlid, guid = session["resource_link_id"], session["consumer_guid"]
    person = next((p for p in db.participants(rlid, guid) if p["user_id"] == user_id), None)
    if person is None:
        return HTMLResponse("No such student in this activity.", status_code=404)
    return templates.TemplateResponse(request, "student_work.html", {
        "session_token": session["token"], "person": person,
        "transcript": evaluation.build_transcript(user_id, rlid, guid),
        "feedback": store.latest_feedback(user_id, rlid, guid),
    })


@router.post("/dashboard/purge")
def purge(request: Request):
    """Delete everything the workshop stored for this activity's students,
    their documents' knowledge bases included. Grades and enrollments stay."""
    session = sessions.require_instructor(request)
    return JSONResponse(retention.purge_activity(session["resource_link_id"], session["consumer_guid"]))


@router.post("/grades/set")
async def grades_set(request: Request):
    session = sessions.require_instructor(request)
    body = await request.json()
    db.set_grade(
        user_id=int(body["user_id"]),
        resource_link_id=session["resource_link_id"],
        consumer_guid=session["consumer_guid"],
        score=float(body["score"]),
        feedback=body.get("feedback", ""),
        proposed_score=body.get("proposed_score"),
    )
    return JSONResponse({"ok": True})


@router.post("/grades/send")
def grades_send(request: Request):
    """Push every approved-but-unsent grade to the LMS via replaceResult."""
    session = sessions.require_instructor(request)
    rows = db.gradable_rows(session["resource_link_id"], session["consumer_guid"])
    sent, failed = 0, []
    for r in rows:
        result = outcomes.send_grade(
            sourcedid=r["lis_result_sourcedid"],
            outcome_url=r["lis_outcome_service_url"],
            score_0_1=float(r["score"]),
            comment=r["feedback"] or "",
        )
        if result["success"]:
            db.mark_grade_sent(r["user_id"], session["resource_link_id"], session["consumer_guid"])
            sent += 1
        else:
            failed.append({"user_id": r["user_id"], "detail": result["detail"]})
    return JSONResponse({"sent": sent, "failed": failed})
