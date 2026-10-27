"""lamb-tool-template — a minimal LAMB-connected, LTI 1.1 educational tool.

Request lifecycle (see docs/anatomy.md):
  LMS  --POST /lti/launch-->  validate  -->  user + session  -->  redirect
       student  --> /workshop/  (the AI Workshop wizard, a Svelte SPA)
       instructor --> /setup (first time) or /dashboard
       instructor --> POST /grades/send  --> replaceResult to the LMS
"""

import asyncio
import logging
import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, settings_store
from .routers import lti_launch, chat, instructor, admin, workshop
from .workshop import retention

# The workshop wizard: the Svelte SPA built into frontend/build (npm run build).
WORKSHOP_SPA = os.getenv("WORKSHOP_SPA_DIR", "frontend/build")

app = FastAPI(title="lamb-tool-template", docs_url=None, redoc_url=None)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    # First-boot convenience: seed runtime settings from the environment if
    # present. Once set via /admin, stored values win. See settings_store.
    settings_store.seed_from_env()


@app.on_event("startup")
async def _start_retention() -> None:
    """Opt-in data retention (WORKSHOP_RETENTION_DAYS): a sweep now, then
    one a day. Without the variable the sweep does nothing. Deletions the
    kb-server has not confirmed yet are retried either way."""
    async def daily():
        while True:
            try:
                await asyncio.to_thread(retention.empty_kb_trash)
                await asyncio.to_thread(retention.sweep)
            except Exception:  # a failed sweep must not stop the next one
                logging.getLogger("workshop.retention").exception("retention sweep failed")
            await asyncio.sleep(24 * 3600)
    app.state.retention_task = asyncio.create_task(daily())


@app.on_event("shutdown")
async def _stop_retention() -> None:
    task = getattr(app.state, "retention_task", None)
    if task:
        task.cancel()


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})


app.include_router(admin.router)
app.include_router(lti_launch.router)
app.include_router(chat.router)
app.include_router(instructor.router)
app.include_router(workshop.router)  # before the SPA mount, which also starts with /workshop

app.mount("/static", StaticFiles(directory="app/static"), name="static")
if os.path.isdir(WORKSHOP_SPA):
    app.mount("/workshop", StaticFiles(directory=WORKSHOP_SPA, html=True), name="workshop")
