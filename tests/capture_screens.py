"""Capture the screenshots used in docs/testing-with-lti-test-tool.md.

Drives the real UIs (the LAMB LTI Test Tool + this tool + LAMB) with a
headless browser and saves full-page PNGs to docs/img/. Re-run whenever the
UI changes to refresh the doc images.

Prereqs (same as tests/e2e_full.py):
  - LAMB up on :9099
  - this tool up on :8090, configured
  - lamb-lti-test-tool up on :8001

Run:  .venv/bin/python tests/capture_screens.py
"""

import os
import sys

from playwright.sync_api import sync_playwright

# reuse the e2e helpers (same directory)
sys.path.insert(0, os.path.dirname(__file__))
from e2e_full import (  # noqa: E402
    ensure_registered, working_assistant, q,
    LMS, TOOL, TEACHER_UID, STUDENT_UID,
)

IMG = os.path.join(os.path.dirname(__file__), "..", "docs", "img")
ADMIN_USER = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PW = os.getenv("ADMIN_PASSWORD", "s3cret-admin")


def shot(page, name):
    path = os.path.join(IMG, name)
    page.screenshot(path=path, full_page=True)
    print("saved", os.path.relpath(path))


def main():
    os.makedirs(IMG, exist_ok=True)
    ct_id = ensure_registered()
    assistant_id, _ = working_assistant()
    print(f"course_tool_id={ct_id}, assistant={assistant_id}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 960},
                                  ignore_https_errors=True)
        page = ctx.new_page()

        # --- this tool: admin console ---
        page.goto(f"{TOOL}/admin/login")
        page.fill("input[name=username]", ADMIN_USER)
        page.fill("input[name=password]", ADMIN_PW)
        page.click("button[type=submit]")
        page.wait_for_url("**/admin")
        shot(page, "01-admin-config.png")

        # --- mock LMS: registration pages ---
        page.goto(f"{LMS}/tool-servers"); page.wait_for_load_state()
        shot(page, "02-lms-tool-servers.png")
        page.goto(f"{LMS}/tools"); page.wait_for_load_state()
        shot(page, "03-lms-tools.png")
        page.goto(f"{LMS}/courses/1"); page.wait_for_load_state()
        shot(page, "04-lms-course.png")

        # --- instructor launch -> setup ---
        page.goto(f"{LMS}/launch/{ct_id}?user_id={TEACHER_UID}")
        page.wait_for_url("**localhost:8090/setup**", timeout=20000)
        page.wait_for_selector("select[name=assistant_model]")
        # taller-ia: no assistant to pick; the engine is set on /admin (T13).
        page.fill("input[name=title]", "AI Study Coach")
        if page.query_selector("input[name=grading_enabled]"):
            page.check("input[name=grading_enabled]")
        shot(page, "05-tool-setup.png")
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**", timeout=20000)

        # --- student launch -> chat, wait for a full reply ---
        sp = ctx.new_page()
        sp.goto(f"{LMS}/launch/{ct_id}?user_id={STUDENT_UID}")
        sp.wait_for_url("**localhost:8090/chat**", timeout=20000)
        sp.fill("#msg", "Hi! Can you help me revise for my Python exam?")
        sp.click("#chat-form button[type=submit]")
        reply = ""
        for _ in range(180):
            bubbles = sp.query_selector_all(".msg.assistant")
            if bubbles:
                reply = bubbles[-1].inner_text().strip()
                if len(reply) > 60 and not reply.startswith("[error"):
                    break
            sp.wait_for_timeout(1000)
        sp.wait_for_timeout(1500)  # let a bit more of the stream render
        shot(sp, "06-tool-chat.png")

        # --- instructor: dashboard, grade, send ---
        page.goto(f"{LMS}/launch/{ct_id}?user_id={TEACHER_UID}")
        page.wait_for_url("**localhost:8090/dashboard**", timeout=20000)
        page.wait_for_selector("tr[data-user]")
        row = page.query_selector_all("tr[data-user]")[0]
        row.query_selector(".score").fill("9")
        row.query_selector(".feedback").fill("Great questions — keep it up.")
        row.query_selector("button.save").click()
        page.wait_for_timeout(600)
        page.click("#send-all")
        for _ in range(20):
            if "Sent" in page.inner_text("#send-result"):
                break
            page.wait_for_timeout(500)
        shot(page, "07-tool-dashboard.png")

        # --- mock LMS: received grade ---
        page.goto(f"{LMS}/grades"); page.wait_for_load_state()
        shot(page, "08-lms-grades.png")

        browser.close()

    print("done — images in docs/img/")


if __name__ == "__main__":
    main()
