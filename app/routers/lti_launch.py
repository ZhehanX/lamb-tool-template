"""The LTI 1.1 launch endpoint — the front door.

Everything upstream of a valid session passes through here: signature
validation, user provisioning, capturing the grade-passback plumbing, and
routing instructor vs student. On a valid launch we mint a session and
redirect (303) to the right in-tool page with the session token on the URL
(the iframe-safe fallback; the cookie is set too)."""

import logging
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import config, db, sessions
from ..lti import validation
from ..workshop import store

logger = logging.getLogger("lti")
router = APIRouter()


@router.post("/lti/launch")
async def lti_launch(request: Request):
    form = dict((await request.form()))
    launch_url = f"{config.PUBLIC_BASE_URL}/lti/launch"

    ok, reason = validation.validate_launch(form, launch_url)
    if not ok:
        logger.warning("Rejected LTI launch: %s", reason)
        return HTMLResponse(
            "<h1>Launch rejected</h1><p>This launch could not be verified. "
            "Check the tool's consumer key and shared secret in your LMS.</p>",
            status_code=401,
        )

    consumer_guid = form.get("tool_consumer_instance_guid", "")
    resource_link_id = form.get("resource_link_id", "")
    roles = form.get("roles", "")
    instructor = validation.is_instructor(roles)

    user_id = db.upsert_user(
        lti_user_id=form.get("user_id", ""),
        consumer_guid=consumer_guid,
        username=form.get("ext_user_username") or form.get("lis_person_sourcedid", ""),
        full_name=form.get("lis_person_name_full", ""),
        email=form.get("lis_person_contact_email_primary", ""),
    )

    # Capture grade-passback plumbing at launch: sourcedid is per-user-per-
    # link and only arrives on the launch, so this is the moment to store it.
    # Students only — the roster and gradebook are for learners, not the
    # instructor configuring the activity.
    if not instructor:
        db.upsert_enrollment(
            user_id=user_id,
            resource_link_id=resource_link_id,
            consumer_guid=consumer_guid,
            lis_result_sourcedid=form.get("lis_result_sourcedid", ""),
            lis_outcome_service_url=form.get("lis_outcome_service_url", ""),
        )

    token = sessions.create_session(user_id, resource_link_id, consumer_guid, instructor)

    activity = db.get_activity(resource_link_id, consumer_guid)
    if instructor:
        dest = "/dashboard" if activity else "/setup"
    else:
        # A student who arrives before the instructor has configured the
        # activity gets a friendly waiting page, not an error.
        dest = "/workshop/" if activity else "/waiting"
        # "Teacher first": students wait until the teacher has tried it.
        if (activity and store.get_settings(resource_link_id, consumer_guid)["teacher_first"]
                and not store.teacher_has_tried(resource_link_id, consumer_guid)):
            dest = "/waiting"

    url = f"{dest}?session={token}"
    # The wizard starts in the LMS language (the old branch's decision C6).
    if locale := form.get("launch_presentation_locale", ""):
        url += f"&lang={quote(locale)}"
    response = RedirectResponse(url=url, status_code=303)
    # SameSite=None so the cookie survives the LMS iframe; Secure required
    # with SameSite=None (works once served over HTTPS, as Atenea is).
    response.set_cookie(
        "tool_session", token, httponly=True, samesite="none", secure=True,
        max_age=config.SESSION_HOURS * 3600,
    )
    return response
