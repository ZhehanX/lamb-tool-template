"""Admin console.

Guarded by a single username + password from the environment
(ADMIN_USERNAME / ADMIN_PASSWORD). The admin sets the runtime configuration
here — LAMB URL + key and the LTI consumer key + secret — and reads off the
LTI launch URL to register in the LMS.

Secrets are write-only in the form: it shows whether each is set, never its
value, and a blank field on save means "leave unchanged"."""

import hmac

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import config, sessions, settings_store

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _check_credentials(username: str, password: str) -> bool:
    # constant-time compare on both fields
    return (
        hmac.compare_digest(username, config.ADMIN_USERNAME)
        and hmac.compare_digest(password, config.ADMIN_PASSWORD)
    )


@router.get("/admin/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "admin_login.html", {"error": None})


@router.post("/admin/login")
async def login(request: Request):
    form = await request.form()
    if not _check_credentials(form.get("username", ""), form.get("password", "")):
        return templates.TemplateResponse(
            request, "admin_login.html", {"error": "Invalid credentials."}, status_code=401
        )
    token = sessions.create_admin_session()
    response = RedirectResponse(url="/admin", status_code=303)
    response.set_cookie("admin_session", token, httponly=True, samesite="lax",
                        max_age=config.ADMIN_SESSION_HOURS * 3600)
    return response


@router.post("/admin/logout")
def logout(request: Request):
    token = request.cookies.get("admin_session")
    if token:
        from ..db import delete_admin_session
        delete_admin_session(token)
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie("admin_session")
    return response


@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    if not sessions.is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    return templates.TemplateResponse(request, "admin.html", {
        "settings": settings_store.public_view(),
        "configured": settings_store.is_configured(),
        "lti_launch_url": config.LTI_LAUNCH_URL,
        "saved": request.query_params.get("saved") == "1",
    })


@router.post("/admin/settings")
async def save_settings(request: Request):
    if not sessions.is_admin(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    form = await request.form()

    # Non-secret values: always take what was submitted (if present).
    for key in ("lamb_api_base", "lti_consumer_key"):
        value = (form.get(key) or "").strip()
        if value:
            settings_store.set_value(key, value)

    # Workshop engine (plan T13): the engine assistant and its context size.
    engine = (form.get("engine_model") or "").strip()
    if engine:
        settings_store.set_value("engine_model", engine)
    context = (form.get("engine_context_tokens") or "").strip()
    if context:
        if not context.isdigit() or not 2048 <= int(context) <= 1_000_000:
            return JSONResponse({"error": "engine_context_tokens must be a number of tokens "
                                          "between 2048 and 1000000"}, status_code=400)
        settings_store.set_value("engine_context_tokens", context)

    # Secret values: update only when a new value is provided; a blank field
    # means "keep the current secret".
    for key in ("lamb_api_key", "lti_secret"):
        value = (form.get(key) or "").strip()
        if value:
            settings_store.set_value(key, value)

    return RedirectResponse(url="/admin?saved=1", status_code=303)
