"""End-to-end test of the AI Workshop wizard: mock LMS -> this tool -> LAMB,
driven with a real browser (plan Phase 5 DoD).

  1. register the tool in the mock LMS and add it to a course (fresh link)
  2. the teacher launches and sets the activity up (template setup page)
  3. a student launches IN ANOTHER BROWSER CONTEXT and lands in the wizard
  4. step 1: instructions; the panel previews them before anything is sent
  5. step 2: a document; the panel shows it whole in the system message
  6. step 4: the calculator; the panel shows its instructions as text
  7. step 5: one chat turn; the panel shows the request, the answer streams;
     then a question that needs the calculator: the tool round shows up, and
     the next turn sends it again as history (T16)

With E2E_ENGINE set to the debug-bypass twin of the engine assistant, the
"answer" is the messages LAMB handed to its connector. The test then checks
they are exactly the messages the panel showed (plan G3 + G4 end to end).
debug-bypass re-splits its text on whitespace when streaming, so the test
messages avoid runs of spaces; newlines survive because JSON escapes them.
The engine set on /admin before the run is put back afterwards.

Prereqs: LAMB on :9099, the mock LMS on :8001, this tool on :8090, and the
same environment as tests/e2e_full.py (E2E_LMS_DB, LTI_CONSUMER_KEY,
LTI_SECRET), plus ADMIN_USERNAME / ADMIN_PASSWORD when E2E_ENGINE is set.

Run:  .venv/bin/python tests/e2e_workshop.py
"""

import contextlib
import html
import json
import os
import re
import sys
import tempfile

import httpx
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(__file__))
import e2e_full  # noqa: E402  (mock-LMS helpers)

TOOL = e2e_full.TOOL
ENGINE = os.getenv("E2E_ENGINE", "")
SHOTS = os.getenv("E2E_SHOTS", os.path.join(tempfile.gettempdir(), "e2e"))
INSTRUCTIONS = "Respon sempre en català.\nSigues breu."
DOCUMENT = "# Apunts de biologia\n\nLa fotosíntesi passa als cloroplasts.\nNecessita llum i aigua."
QUESTION = "On passa la fotosíntesi?"

results = []
check = e2e_full.check
e2e_full.results = results


@contextlib.contextmanager
def admin_client():
    with httpx.Client(base_url=TOOL) as c:
        c.post("/admin/login", data={"username": os.environ["ADMIN_USERNAME"],
                                     "password": os.environ["ADMIN_PASSWORD"]})
        yield c


def set_engine(model: str) -> None:
    with admin_client() as c:
        r = c.post("/admin/settings", data={"engine_model": model})
        assert r.status_code == 303, r.text


def current_engine() -> str:
    """The engine on /admin, read back so the run can restore it."""
    with admin_client() as c:
        m = re.search(r'name="engine_model" value="([^"]*)"', c.get("/admin").text)
    return html.unescape(m.group(1)) if m else ""


def next_step(page, expected: int) -> None:
    """Press the wizard's Next button (not any text containing "next")."""
    page.get_by_role("button", name="Next →").click()
    page.wait_for_function(
        "n => document.body.textContent.includes(`Step ${n} of 6`)", arg=expected, timeout=10000)


def panel_request(page, testid: str = "panel-request") -> dict:
    """The request body exactly as the panel prints it."""
    raw = page.locator(f'[data-testid="{testid}"] pre').text_content()
    return json.loads(raw)


def main():
    # A debug-engine run used to leave the dev stack on the echoing engine.
    previous = current_engine() if ENGINE else ""
    if ENGINE:
        set_engine(ENGINE)
    try:
        run()
    finally:
        if previous and previous != ENGINE:
            set_engine(previous)


def run():
    ct_id = e2e_full.ensure_registered()
    check("registered tool in mock LMS", bool(ct_id), f"course_tool_id={ct_id}")

    doc_path = os.path.join(tempfile.gettempdir(), "apunts-e2e.md")
    with open(doc_path, "w", encoding="utf-8") as f:
        f.write(DOCUMENT)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # The teacher sets the activity up in one browser context...
        teacher = browser.new_context().new_page()
        teacher.goto(e2e_full.launch_url(ct_id, e2e_full.TEACHER_UID))
        teacher.wait_for_url("**/setup**", timeout=20000)
        teacher.fill("input[name=title]", "Taller d'IA (e2e)")
        teacher.click("button[type=submit]")
        teacher.wait_for_url("**/dashboard**", timeout=20000)
        check("teacher set the activity up", "/dashboard" in teacher.url)

        # ...the student works in another one: the template's cookie would
        # otherwise switch identities between tabs (plan §0.1).
        page = browser.new_context().new_page()
        page.goto(e2e_full.launch_url(ct_id, e2e_full.STUDENT_UID))
        page.wait_for_url("**/workshop/**", timeout=20000)
        check("student lands in the wizard", "/workshop/?session=" in page.url, page.url)
        page.wait_for_selector('[data-testid="consent"]', timeout=20000)
        check("the consent notice comes first, naming LAMB's logs",
              "LAMB may record the beginning of each message" in page.text_content('[data-testid="consent"]'))
        page.get_by_test_id("consent-accept").click()
        page.wait_for_selector("#ws-instructions", timeout=20000)

        # Step 1
        page.fill("#ws-instructions", INSTRUCTIONS)
        page.wait_for_function(
            """t => [...document.querySelectorAll('[data-testid=panel-part][data-source=instructions]')]
                     .some(e => e.textContent.includes(t))""", arg="Sigues breu.", timeout=15000)
        check("step 1: the panel previews the instructions before anything is sent",
              # text_content: inner_text applies the heading's CSS uppercase
              "What your next message would send" in page.text_content('[data-testid="panel-request"]'))
        next_step(page, 2)

        # Step 2. Wait for the upload itself, not only the card: a design save
        # made meanwhile already returns the stored document, and an upload
        # still running (a cold kb-server) refreshes the panel after the turn.
        with page.expect_response(lambda r: r.url.endswith("/workshop/api/document")
                                  and r.request.method == "POST", timeout=60000):
            page.set_input_files("input[type=file]", doc_path)
        page.wait_for_selector('[data-testid="document-card"]', timeout=20000)
        check("step 2: the document is attached", "apunts-e2e.md" in page.inner_text('[data-testid="document-card"]'))
        page.wait_for_selector('[data-testid=panel-part][data-source=document]', timeout=15000)
        system = panel_request(page)["messages"][0]
        check("step 2: the whole document is in the system message",
              system["role"] == "system" and DOCUMENT in system["content"])
        page.screenshot(path=f"{SHOTS}-workshop-step2.png", full_page=True)
        next_step(page, 3)
        next_step(page, 4)

        # Step 4
        page.get_by_label("Calculator").check()
        page.wait_for_function(
            "() => document.querySelector('[data-testid=panel-tools]')?.textContent.includes('calculator')",
            timeout=15000)
        check("step 4: the panel shows the tool instructions the model will read",
              '<tool name="TOOL_NAME">' in page.text_content('[data-testid="panel-tools"]'))
        next_step(page, 5)
        check("the wizard reached step 5", "Step 5 of 6" in page.text_content("body"))

        # Step 5
        page.fill('[data-testid="chat-input"]', QUESTION)
        page.click('[data-testid="chat-send"]')
        page.wait_for_selector('[data-testid="panel-answered-by"]', timeout=180000)
        answer = page.locator('[data-testid="chat-assistant"]').last.inner_text().strip()
        sent = panel_request(page, "panel-result")
        check("step 5: an answer arrived", bool(answer) and not page.locator('[data-testid="chat-error"]').count(),
              answer[:80])
        check("step 5: the panel shows the request with history, document and question",
              [m["role"] for m in sent["messages"]] == ["system", "user"]
              and sent["messages"][1]["content"] == QUESTION and sent["model"] == (ENGINE or sent["model"]))
        check("step 5: the panel names the model that answered",
              "Answered by:" in page.inner_text('[data-testid="panel-answered-by"]'))
        page.screenshot(path=f"{SHOTS}-workshop-step5.png", full_page=True)

        if answer.startswith("Messages:"):
            # debug-bypass: the answer is what LAMB gave its connector.
            echoed = json.loads(answer[len("Messages:"):answer.index("Body:")])
            check("G4 end to end: LAMB received exactly what the panel showed",
                  echoed == sent["messages"], json.dumps(echoed, ensure_ascii=False)[:120])

        if not answer.startswith("Messages:"):
            # A real model: a question that needs the calculator.
            page.fill('[data-testid="chat-input"]', "Quant fa 17 × 23?")
            page.click('[data-testid="chat-send"]')
            page.wait_for_function(
                "() => document.querySelectorAll('[data-testid=chat-assistant]').length >= 2"
                " && !document.querySelector('[data-testid=model-thinking]')"
                " && document.querySelector('[data-testid=chat-send]')", timeout=180000)
            page.wait_for_selector('[data-testid="tool-event"]', timeout=10000)
            timeline = page.text_content("body")
            final = page.locator('[data-testid="chat-assistant"]').last.inner_text()
            check("step 5: the calculator ran and its result is in the timeline", "→ 391" in timeline)
            check("step 5: the answer uses the result", "391" in final, final[:80])
            page.screenshot(path=f"{SHOTS}-workshop-tool.png", full_page=True)

            # T16: the next turn sends that tool round again, as the model saw it.
            follow_up = "I el doble?"
            page.fill('[data-testid="chat-input"]', follow_up)
            page.click('[data-testid="chat-send"]')
            page.wait_for_function(
                "() => document.querySelectorAll('[data-testid=chat-assistant]').length >= 3"
                " && !document.querySelector('[data-testid=model-thinking]')"
                " && document.querySelector('[data-testid=chat-send]')", timeout=180000)
            page.wait_for_selector('[data-testid="panel-answered-by"]', timeout=10000)
            messages = panel_request(page, "panel-result")["messages"]
            asked = next((i for i, m in enumerate(messages) if m["content"] == follow_up), len(messages))
            earlier = messages[:asked]
            check("next turn: the earlier tool call and its result are sent again (T16)",
                  any(m["role"] == "assistant" and "<tool" in m["content"] for m in earlier)
                  and any(m["role"] == "user" and m["content"].startswith('<tool_result name="calculator">')
                          and "391" in m["content"] for m in earlier),
                  json.dumps([m["content"][:40] for m in earlier], ensure_ascii=False)[:200])
            check("next turn: the panel labels them as an earlier tool round",
                  "Earlier tool call written by the model" in page.text_content('[data-testid="panel-segments"]'))

        # Step 6: reflection, submission, formative feedback.
        next_step(page, 6)
        page.fill('[data-testid="reflection-q1"]', "Volia un tutor breu que respongués en català.")
        page.fill('[data-testid="reflection-q2"]', "Rebia les meves instruccions i tot el document al missatge de sistema.")
        page.fill('[data-testid="reflection-q3"]', "Afegiria exemples a les instruccions.")
        page.get_by_role("button", name="Submit workshop").click()
        page.wait_for_selector('[data-testid="submitted"]', timeout=20000)
        page.wait_for_function(
            "() => { const f = document.querySelector('[data-testid=feedback]');"
            " return f && !/Generating/.test(f.textContent); }", timeout=300000)
        feedback = page.text_content('[data-testid="feedback"]')
        if answer.startswith("Messages:"):
            # debug-bypass echoes instead of grading: the feedback can only fail.
            check("step 6: the feedback request reached the engine and its failure is shown",
                  "could not be generated" in feedback.lower(), feedback[:120])
        else:
            check("step 6: formative feedback per rubric criterion, not a grade",
                  "not a grade" in feedback and "Reflection" in feedback, feedback[:160])
        page.screenshot(path=f"{SHOTS}-workshop-feedback.png", full_page=True)

        # The same student in a reloaded tab gets the turn back from the server.
        page.reload()
        page.wait_for_selector('[data-testid="chat-user"]', timeout=20000)
        check("reload: the conversation is restored", QUESTION in page.inner_text('[data-testid="chat-messages"]'))
        browser.close()

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
