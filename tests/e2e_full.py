"""Full-stack end-to-end test: mock LMS -> this tool -> LAMB, driven with a
real browser.

Exercises the whole loop the way a real deployment runs:
  1. register this tool in the LAMB LTI Test Tool (the mock LMS)
  2. probe LAMB for an assistant that actually answers
  3. instructor launches -> setup -> pick the assistant -> dashboard
  4. student launches -> chat -> send a message -> assistant streams a reply
  5. instructor re-launches -> grades the student -> pushes the grade
  6. confirm the mock LMS received the grade (replaceResult round-trip)

Prereqs (all on the host):
  - LAMB up on :9099
  - LAMB LTI Test Tool up on :8001  (uvicorn app:app --port 8001 in
    /opt/lamb-lti-test-tool; default 8000 collides with GLM's llama-server)
  - this tool up on :8090, configured with the LTI creds below

Run:  .venv/bin/python tests/e2e_full.py

The constants below can be overridden from the environment (E2E_LMS_DB,
E2E_LAMB_KEY, LTI_CONSUMER_KEY, LTI_SECRET, E2E_SHOTS, E2E_ASSISTANT), so
the test also runs on a dev box that is not laid out like the one above.
E2E_ASSISTANT pins the assistant instead of probing /v1/models.
"""

import os
import sqlite3
import sys
import tempfile

import httpx
from playwright.sync_api import sync_playwright

LMS = "http://localhost:8001"
TOOL = "http://localhost:8090"
LMS_DB = os.getenv("E2E_LMS_DB", "/opt/lamb-lti-test-tool/lti_platform.db")
LAMB_BASE = "http://localhost:9099"
LAMB_KEY = os.getenv("E2E_LAMB_KEY", "0p3n-w3bu!")

CONSUMER_KEY = os.getenv("LTI_CONSUMER_KEY", "lamb-tool-key")
CONSUMER_SECRET = os.getenv("LTI_SECRET", "lamb-tool-secret-2026")
COURSE_ID = 1
TEACHER_UID = 1   # Dr. Alice Smith (role=teacher -> Instructor)
STUDENT_UID = 3   # Charlie Brown  (role=student -> Learner)
SHOTS = os.getenv("E2E_SHOTS", os.path.join(tempfile.gettempdir(), "e2e"))

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("· " + str(extra)) if extra else "")


def q(sql, args=()):
    c = sqlite3.connect(LMS_DB)
    c.row_factory = sqlite3.Row
    rows = c.execute(sql, args).fetchall()
    c.close()
    return rows


# --- phase 1: register this tool in the mock LMS (idempotent) ----------------

def ensure_registered():
    rows = q("SELECT id FROM tool_servers WHERE domain='localhost' AND port=8090")
    if rows:
        server_id = rows[0]["id"]
    else:
        httpx.post(f"{LMS}/tool-servers/add", follow_redirects=True, data={
            "name": "lamb-tool-template", "domain": "localhost", "port": 8090,
            "description": "e2e"})
        server_id = q("SELECT id FROM tool_servers WHERE domain='localhost' AND port=8090")[0]["id"]

    rows = q("SELECT id FROM tools WHERE consumer_key=?", (CONSUMER_KEY,))
    if rows:
        tool_id = rows[0]["id"]
    else:
        httpx.post(f"{LMS}/tools/add", follow_redirects=True, data={
            "name": "LAMB-TOOL-TEMPLATE", "tool_server_id": server_id,
            "launch_path": "/lti/launch", "consumer_key": CONSUMER_KEY,
            "consumer_secret": CONSUMER_SECRET})
        tool_id = q("SELECT id FROM tools WHERE consumer_key=?", (CONSUMER_KEY,))[0]["id"]

    # Remove any prior course-tool for this (course, tool) — the mock LMS has
    # a unique (course_id, tool_id) constraint — then add a FRESH one. The new
    # resource_link_id is one the template has never seen, so every run
    # exercises the true first-time flow: instructor -> setup.
    for row in q("SELECT id FROM course_tools WHERE course_id=? AND tool_id=?", (COURSE_ID, tool_id)):
        httpx.get(f"{LMS}/courses/{COURSE_ID}/tools/{row['id']}/remove", follow_redirects=True)
    httpx.post(f"{LMS}/courses/{COURSE_ID}/tools/add", follow_redirects=True,
               data={"tool_id": tool_id})
    ct_id = q("SELECT id FROM course_tools WHERE course_id=? AND tool_id=? ORDER BY id DESC",
              (COURSE_ID, tool_id))[0]["id"]
    return ct_id


# --- phase 2: find a LAMB assistant that actually answers --------------------

def working_assistant():
    pinned = os.getenv("E2E_ASSISTANT")
    if pinned:
        return pinned, "(pinned by E2E_ASSISTANT)"
    data = httpx.get(f"{LAMB_BASE}/v1/models",
                     headers={"Authorization": f"Bearer {LAMB_KEY}"}, timeout=30).json()["data"]
    # Prefer text assistants — image-generation assistants answer a text
    # prompt with an image error, which is a poor chat demo.
    text_first = sorted(data, key=lambda m: bool(m.get("capabilities", {}).get("image_generation")))
    for m in text_first:
        try:
            r = httpx.post(f"{LAMB_BASE}/v1/chat/completions",
                           headers={"Authorization": f"Bearer {LAMB_KEY}"},
                           json={"model": m["id"],
                                 "messages": [{"role": "user", "content": "Reply with a short greeting."}],
                                 "stream": False}, timeout=90)
            if r.status_code == 200:
                content = (r.json()["choices"][0]["message"].get("content", "") or "").strip()
                # skip empty and error-shaped replies (image assistants, etc.)
                if content and "error" not in content.lower() and not content.startswith("❌"):
                    return m["id"], content[:70]
        except Exception:
            continue
    return (data[0]["id"] if data else None), None


def launch_url(ct_id, uid):
    return f"{LMS}/launch/{ct_id}?user_id={uid}"


def main():
    ct_id = ensure_registered()
    check("registered tool in mock LMS", bool(ct_id), f"course_tool_id={ct_id}")

    assistant_id, sample = working_assistant()
    check("found a responding LAMB assistant", bool(sample), f"{assistant_id}: {sample!r}")
    if not assistant_id:
        print("No assistant available — aborting."); sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(ignore_https_errors=True)

        # 1. instructor launch -> setup
        page = ctx.new_page()
        page.goto(launch_url(ct_id, TEACHER_UID))
        page.wait_for_url("**localhost:8090/**", timeout=20000)
        check("instructor lands on tool", "localhost:8090" in page.url, page.url)
        check("instructor -> setup", "/setup" in page.url)

        page.wait_for_selector("input[name=title]")
        # taller-ia: no assistant to pick; the engine is set on /admin (T13).
        page.fill("input[name=title]", "E2E Chat Activity")
        if page.query_selector("input[name=grading_enabled]"):
            page.check("input[name=grading_enabled]")
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**", timeout=20000)
        check("setup saved -> dashboard", "/dashboard" in page.url)
        page.screenshot(path=f"{SHOTS}-1-instructor-dashboard.png")

        # 2. student launch -> chat -> send a message -> streamed reply
        sp = ctx.new_page()
        sp.goto(launch_url(ct_id, STUDENT_UID))
        sp.wait_for_url("**localhost:8090/**", timeout=20000)
        # taller-ia: students land in the workshop wizard (tests/e2e_workshop.py
        # covers it). The template's chat page still exists and feeds the
        # template roster and grades, so the grade round-trip goes through it.
        check("student -> workshop", "/workshop/?session=" in sp.url, sp.url)
        sp.goto(sp.url.replace("/workshop/?", "/chat?"))

        sp.wait_for_selector("#msg")
        sp.fill("#msg", "Hello! In one short sentence, what can you help me with?")
        sp.click("#chat-form button[type=submit]")

        # LAMB assistants call real LLMs — first-token + full-stream latency
        # can run to a minute or more, so poll generously.
        reply = ""
        for _ in range(180):
            bubbles = sp.query_selector_all(".msg.assistant")
            if bubbles:
                reply = bubbles[-1].inner_text().strip()
                if reply and not reply.startswith("[error"):
                    break
            sp.wait_for_timeout(1000)
        check("assistant streamed a reply", bool(reply) and not reply.startswith("[error"), reply[:70])
        sp.screenshot(path=f"{SHOTS}-2-student-chat.png")

        # 3. instructor re-launch -> dashboard -> grade the student -> send
        page.goto(launch_url(ct_id, TEACHER_UID))
        page.wait_for_url("**localhost:8090/**", timeout=20000)
        check("configured re-launch -> dashboard", "/dashboard" in page.url, page.url)

        page.wait_for_selector("tr[data-user]")
        rows = page.query_selector_all("tr[data-user]")
        # grade the student's row (find by name; roster is students only)
        target = None
        for r in rows:
            if "Charlie" in r.inner_text():
                target = r
                break
        target = target or (rows[0] if rows else None)
        check("student appears in roster", target is not None, f"{len(rows)} row(s)")

        if target:
            target.query_selector(".score").fill("8")
            target.query_selector(".feedback").fill("Good engagement (e2e).")
            target.query_selector("button.save").click()
            page.wait_for_timeout(600)
            # Save -> reload -> save again must keep 8/10 (the template used to
            # show the stored 0-1 value and divide it by ten once more).
            page.reload()
            page.wait_for_selector("tr[data-user]")
            target = [r for r in page.query_selector_all("tr[data-user]") if "Charlie" in r.inner_text()][0]
            shown = target.query_selector(".score").input_value()
            check("reloaded grade still reads 8", shown == "8", shown)
            target.query_selector("button.save").click()
            page.wait_for_timeout(600)
            page.click("#send-all")
            send_text = ""
            for _ in range(20):
                send_text = page.inner_text("#send-result")
                if "Sent" in send_text:
                    break
                page.wait_for_timeout(500)
            check("grade pushed to LMS", "Sent 1" in send_text, send_text)
            page.screenshot(path=f"{SHOTS}-3-graded.png")

        browser.close()

    # 4. confirm the mock LMS stored the grade (replaceResult round-trip)
    grows = q("SELECT score, sourced_id, received_at FROM grade_results "
              "WHERE course_tool_id=? ORDER BY id DESC LIMIT 1", (ct_id,))
    check("mock LMS received the grade", bool(grows),
          dict(grows[0]) if grows else "no grade row")
    if grows:
        check("grade value == 0.8", abs(grows[0]["score"] - 0.8) < 0.001, grows[0]["score"])

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    print(f"screenshots: {SHOTS}-1-instructor-dashboard.png, {SHOTS}-2-student-chat.png, {SHOTS}-3-graded.png")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
