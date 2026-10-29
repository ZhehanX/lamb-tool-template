"""The AI Workshop's JSON / SSE API, used by the Svelte wizard at /workshop/.

The SPA sends the session token in the X-Session-Token header and never
sends cookies (fetch credentials 'omit'): the template reads the
`tool_session` cookie first, and a later launch in the same browser (a
student after the teacher) overwrites it (plan §0.1)."""

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from .. import db, sessions, settings_store
from ..workshop import documents, kb_client, retention, service, store
from ..workshop.assembler import ContextOverflow

router = APIRouter(prefix="/workshop/api")


def _error(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse({"detail": message, "code": code, **extra}, status_code=status)


def _consented(request: Request):
    """The session, if its user accepted the current consent notice; every
    route that stores the student's work or sends it to the model uses it."""
    session = sessions.resolve_session(request)
    if not service.has_consented(session):
        raise HTTPException(status_code=403, detail="Read and accept the notice first.")
    return session


@router.get("/state")
def state(request: Request):
    session = sessions.resolve_session(request)
    uid, rlid, guid = service.scope(session)
    activity = db.get_activity(rlid, guid)
    b = service.budget()
    return {
        "role": "instructor" if session["is_instructor"] else "student",
        "activity": {"title": activity["title"] if activity else None,
                     "configured": activity is not None},
        "assistant": service.assistant_view(store.get_assistant(uid, rlid, guid)),
        "turns": [service.turn_view(t) for t in store.get_turns(uid, rlid, guid)],
        "reflection": store.get_reflection(uid, rlid, guid),
        "feedback": store.latest_feedback(uid, rlid, guid),
        # Only turns of the current conversation are sent as history.
        "conversation": store.conversation(uid, rlid, guid),
        "engine": {"configured": bool(settings_store.get("engine_model"))},
        "kb": service.kb_status(uid, rlid, guid),
        "budget": {"context_tokens": b.context_tokens, "input_limit": b.input_limit,
                   "tokenizer": service.counter().name, "input_caps": service.input_caps()},
        "limits": {"document_bytes": documents.MAX_BYTES, "document_types": documents.ACCEPTED},
        "consent": {"version": service.CONSENT_VERSION, "accepted": service.has_consented(session),
                    "model_note": store.get_settings(rlid, guid)["consent_model_note"],
                    "retention_days": retention.retention_days()},
    }


@router.put("/consent")
async def consent(request: Request):
    session = sessions.resolve_session(request)
    body = await request.json()
    if body.get("version") != service.CONSENT_VERSION:
        return _error(409, "consent_outdated", "The notice changed; reload the page and read it again.")
    store.save_consent(*service.scope(session), service.CONSENT_VERSION)
    return {"accepted": True, "version": service.CONSENT_VERSION}


@router.put("/assistant")
async def save_assistant(request: Request):
    session = _consented(request)
    body = await request.json()
    fields = {}
    if isinstance(body.get("instructions"), str):
        fields["instructions"] = body["instructions"]
    if isinstance(body.get("tools"), list):
        fields["tools"] = [t for t in body["tools"] if isinstance(t, str)]
    if isinstance(body.get("current_step"), int) and 1 <= body["current_step"] <= 6:
        fields["current_step"] = body["current_step"]
    if isinstance(body.get("retrieval_enabled"), bool):
        # Step 3: search the step-2 document instead of sending it whole (or not).
        fields["retrieval_enabled"] = int(body["retrieval_enabled"])
    if fields:
        store.save_assistant(*service.scope(session), **fields)
    return service.assistant_view(store.get_assistant(*service.scope(session)))


def _document_view(scope) -> dict:
    """The assistant after a document change, and where its search stands."""
    return {**service.assistant_view(store.get_assistant(*scope)), "kb": service.kb_status(*scope)}


@router.post("/document")
async def upload_document(request: Request, file: UploadFile = File(...)):
    """Step 2. The text also becomes the student's knowledge base for step 3
    (T15); a new document starts out sent whole until it is connected there."""
    session = _consented(request)
    data = await file.read(documents.MAX_BYTES + 1)
    try:
        text = documents.extract_text(file.filename or "", data)
    except documents.DocumentError as e:
        return _error(413 if e.code == "too_large" else 422, e.code, e.message)
    scope = service.scope(session)
    name = file.filename or "document"
    store.save_assistant(*scope, document_name=name, document_text=text,
                         document_bytes=len(data), retrieval_enabled=0)

    def index():  # blocking kb-server calls: off the event loop
        service.index_document(*scope, name, text)
        return _document_view(scope)
    return await run_in_threadpool(index)


@router.delete("/document")
def remove_document(request: Request):
    session = sessions.resolve_session(request)
    scope = service.scope(session)
    store.save_assistant(*scope, document_name=None, document_text=None,
                         document_bytes=None, retrieval_enabled=0)
    retention.drop_student_kb(*scope)
    return _document_view(scope)


@router.get("/kb")
def kb(request: Request):
    """Step 3: where the search of the student's document stands (the wizard
    polls this while the kb-server is still splitting and embedding it)."""
    session = sessions.resolve_session(request)
    return service.kb_status(*service.scope(session))


def _input_cap(body: dict) -> int | None:
    """The limit the student chose in step 5 (T18); service.budget keeps only
    the listed ones."""
    try:
        return int(body.get("input_cap") or 0) or None
    except (TypeError, ValueError):
        return None


@router.post("/preview")
async def preview(request: Request):
    session = sessions.resolve_session(request)
    body = await request.json() if await request.body() else {}
    try:
        return service.preview(session, str(body.get("message") or ""),
                               "off" if body.get("rag_mode") == "off" else "on",
                               _input_cap(body))
    except ContextOverflow as e:
        return _error(413, "context_overflow", str(e), needed=e.needed, limit=e.limit,
                      hint=service.overflow_hint(e))


@router.post("/probe")
async def probe(request: Request):
    """Step 3: what a test question retrieves from the student's document."""
    session = _consented(request)
    body = await request.json()
    query = (body.get("query") or "").strip()
    if not query:
        return _error(400, "empty", "Empty question.")
    try:
        return service.probe(session, query)
    except service.NotReady as e:
        return _error(409, "not_ready", str(e))
    except kb_client.KbError as e:
        return _error(502, "kb_unavailable", str(e))


@router.post("/chat")
async def chat(request: Request):
    session = _consented(request)
    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        return _error(400, "empty", "Empty message.")
    rag_mode = "off" if body.get("rag_mode") == "off" else "on"
    try:
        stream = service.start_chat(session, message, rag_mode, _input_cap(body))
    except service.NotReady as e:
        return _error(409, "not_ready", str(e))
    except service.RateLimited as e:
        return _error(429, "rate_limited", str(e))
    except ContextOverflow as e:
        return _error(413, "context_overflow",
                      "This message does not fit in what the model can read, even without "
                      "the document and the earlier messages. Write a shorter message.",
                      needed=e.needed, limit=e.limit, hint=service.overflow_hint(e))
    return StreamingResponse(stream, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/conversation/new")
def new_conversation(request: Request):
    """The turns so far stay stored but are no longer sent as history."""
    session = sessions.resolve_session(request)
    return {"conversation": store.new_conversation(*service.scope(session))}


@router.put("/reflection")
async def save_reflection(request: Request):
    session = _consented(request)
    body = await request.json()
    answers = body.get("answers")
    if not isinstance(answers, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in answers.items()):
        return _error(400, "invalid", "answers must map question keys to text.")
    store.save_reflection(*service.scope(session), answers, submit=body.get("submit") is True)
    return store.get_reflection(*service.scope(session))


@router.post("/feedback")
def feedback(request: Request):
    """Step 6: formative feedback on the submitted work (a suggestion, never a
    grade). Slow: one non-streamed call to the engine."""
    session = _consented(request)
    reflection = store.get_reflection(*service.scope(session))
    if not reflection or not reflection.get("submitted_at"):
        return _error(409, "not_submitted", "Submit the workshop first.")
    try:
        return service.formative_feedback(session)
    except service.NotReady as e:
        return _error(409, "not_ready", str(e))


@router.get("/traces/{trace_id}")
def trace(request: Request, trace_id: int):
    session = sessions.resolve_session(request)
    data = store.get_trace_for(trace_id, *service.scope(session))
    if data is None:
        return _error(404, "not_found", "No such trace.")
    return data
