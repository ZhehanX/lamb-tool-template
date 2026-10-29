"""End-to-end test of step 3, the student's own document searched as a
knowledge base (plan Phase 8, G7, T15): mock LMS -> this tool -> its own
kb-server (+ LAMB for the chat), in a real browser.

  1. the teacher sets the activity up (there is no course material to upload)
  2. the student, in another browser context, attaches a document in step 2;
     step 3 waits until the kb-server has split and embedded it, then a test
     question lists every candidate, the selected ones and the cut line
  3. the student connects it and asks the same question in step 5 with the
     knowledge base on and off: "on" sends excerpts instead of the document,
     "off" the whole document and no excerpts; the comparison shows both
  4. removing the document in step 2 deletes its collection in the kb-server

Prereqs: as tests/e2e_workshop.py, plus the tool's kb-server
(docker-compose.dev.yaml, published on 127.0.0.1:9190 or E2E_KB_URL) and
KB_SERVER_TOKEN in the environment.

Run:  .venv/bin/python tests/e2e_document_kb.py
"""

import json
import os
import sys
import tempfile

import httpx
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(__file__))
import e2e_full  # noqa: E402
from e2e_workshop import next_step  # noqa: E402

SHOTS = os.getenv("E2E_SHOTS", os.path.join(tempfile.gettempdir(), "e2e"))
KB_URL = os.getenv("E2E_KB_URL", "http://127.0.0.1:9190")
MATERIAL = """# El cicle de l'aigua

L'energia del sol evapora l'aigua de superfície. Una molècula d'aigua roman uns
deu dies en l'atmosfera. En ascendir, baixa la temperatura, per la qual cosa el
vapor es condensa en petites gotes que formen els núvols.

Precipitació: es produeix quan les gotes d'aigua dels núvols es refreden, s'uneixen
i formen gotes més grosses que acaben caient en forma de pluja, neu o calamarsa.

Infiltració: ocorre quan l'aigua que arriba al sòl penetra a través dels seus porus
i passa a ser subterrània. La permeabilitat del sòl influeix en la capacitat
d'infiltració.

# La fotosíntesi

Les plantes fan la fotosíntesi als cloroplasts. Una cèl·lula vegetal típica conté
entre 10 i 100 cloroplasts. La clorofil·la és el principal pigment que absorbeix la
llum i dona el color verd a les plantes.
"""
QUESTION = "Quant de temps es queda una molècula d'aigua a l'atmosfera?"

results = []
check = e2e_full.check
e2e_full.results = results


def student_collections() -> set[int]:
    """The ids of the students' document collections in the kb-server."""
    r = httpx.get(f"{KB_URL}/collections", params={"limit": 1000},
                  headers={"Authorization": f"Bearer {os.environ['KB_SERVER_TOKEN']}"})
    r.raise_for_status()
    return {c["id"] for c in r.json().get("items", []) if c["name"].startswith("student-")}


def main():
    ct_id = e2e_full.ensure_registered()
    # E2E_KB_FILE: a real document instead of the short built-in text, to
    # see many candidates and the top-k cut line.
    path = os.getenv("E2E_KB_FILE") or os.path.join(tempfile.gettempdir(), "apunts-e2e.md")
    if not os.getenv("E2E_KB_FILE"):
        with open(path, "w", encoding="utf-8") as f:
            f.write(MATERIAL)
    name = os.path.basename(path)
    before = student_collections()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # 1. Teacher: the activity, nothing else
        teacher = browser.new_context().new_page()
        teacher.goto(e2e_full.launch_url(ct_id, e2e_full.TEACHER_UID))
        teacher.wait_for_url("**/setup**", timeout=20000)
        teacher.fill("input[name=title]", "Taller d'IA (KB e2e)")
        teacher.click("button[type=submit]")
        teacher.wait_for_url("**/dashboard**", timeout=20000)
        check("teacher: no course knowledge base to manage any more",
              teacher.locator("a[href^='/course-kb']").count() == 0)

        # 2. Student: step 2 document, step 3 test question
        page = browser.new_context().new_page()
        page.goto(e2e_full.launch_url(ct_id, e2e_full.STUDENT_UID))
        page.get_by_test_id("consent-accept").click()
        page.wait_for_selector("#ws-instructions", timeout=20000)
        page.fill("#ws-instructions", "Respon en català, en una frase.")
        next_step(page, 2)
        page.set_input_files("input[type=file]", path)
        page.wait_for_selector('[data-testid="document-card"]', timeout=20000)
        next_step(page, 3)
        page.wait_for_selector('[data-testid="kb-ready"]', timeout=180000)
        ready = page.text_content('[data-testid="kb-ready"]')
        check("step 3: it searches the step-2 document, once split", name in ready and "excerpts" in ready, ready)
        mine = student_collections() - before
        check("step 3: the document has its own collection in the kb-server", len(mine) == 1, str(mine))
        page.fill("#ws-kb-query", QUESTION)
        page.click("text=Test retrieval")
        page.wait_for_selector('[data-testid="panel-probe-candidate"]', timeout=60000)
        candidates = page.locator('[data-testid="panel-probe-candidate"]')
        selected = page.locator('[data-testid="panel-probe-candidate"][data-selected="true"]')
        check("step 3: every candidate is listed, the top 3 are selected",
              candidates.count() >= selected.count() == min(3, candidates.count()),
              f"{selected.count()} of {candidates.count()}")
        # The criterion of scripts/compare_embeddings.py: the passage that
        # answers is among the excerpts that go into the prompt.
        check("step 3: the right excerpt is among the selected ones, from this document",
              any("deu dies" in t and name in t for t in selected.all_inner_texts()),
              selected.first.inner_text()[:90])
        check("step 3: chunking and embedding are shown",
              "1000" in page.text_content('[data-testid="panel-probe-info"]'))
        page.screenshot(path=f"{SHOTS}-kb-step3.png", full_page=True)
        page.get_by_test_id("kb-connect").check()

        # 3. Step 5: the same question with the knowledge base on and off
        next_step(page, 4)
        next_step(page, 5)

        def show(view):
            """The chat card shows the chat or, once there is a run, the comparison (T17)."""
            tab = page.get_by_test_id(f"{view}-tab")
            if tab.count():
                tab.click()

        def ask(mode):
            """Send QUESTION in a mode; return the request as the panel shows it."""
            show("chat")
            page.get_by_test_id(f"rag-mode-{mode}").click()
            answers = page.locator('[data-testid="chat-assistant"]').count()
            page.fill('[data-testid="chat-input"]', QUESTION)
            page.click('[data-testid="chat-send"]')
            # Wait for THIS turn: one more answer, and the input is back.
            page.wait_for_function(
                "n => document.querySelectorAll('[data-testid=chat-assistant]').length > n"
                " && document.querySelector('[data-testid=chat-send]')"
                " && !document.querySelector('[data-testid=model-thinking]')",
                arg=answers, timeout=180000)
            return json.loads(page.locator('[data-testid="panel-result"] pre').text_content())

        sent = ask("on")
        system, question = sent["messages"][0]["content"], sent["messages"][-1]["content"]
        check("step 5 on: the excerpts are in the request, before the question",
              question.startswith("Excerpts from the document:") and "deu dies" in question)
        check("step 5 on: the document is not sent whole", "deu dies" not in system, system[:120])
        check("step 5 on: the panel lists the turn's candidates",
              page.locator('[data-testid="panel-retrieval-candidate"]').count() > 0)

        # Off in the same conversation: the earlier answer rides along as history.
        sent = ask("off")
        check("step 5 off: the whole document, the question as written",
              "deu dies" in sent["messages"][0]["content"] and sent["messages"][-1]["content"] == QUESTION)
        check("same conversation: the 'off' request carries the 'on' answer as history",
              [m["role"] for m in sent["messages"]] == ["system", "user", "assistant", "user"])
        show("compare")
        page.wait_for_selector('[data-testid="comparison-same-conversation"]', timeout=10000)
        check("comparison: it warns that the runs share a conversation", True)

        # A new conversation makes the comparison clean.
        page.get_by_test_id("new-conversation").click()
        page.wait_for_selector('[data-testid="chat-divider"]', timeout=10000)
        sent = ask("off")
        check("new conversation: the 'off' request has no earlier messages",
              [m["role"] for m in sent["messages"]] == ["system", "user"])
        show("compare")
        page.wait_for_selector('[data-testid="comparison-on"]', timeout=10000)
        check("comparison: no warning once the runs are in different conversations",
              page.locator('[data-testid="comparison-same-conversation"]').count() == 0)
        on = page.inner_text('[data-testid="comparison-on"]')
        off = page.inner_text('[data-testid="comparison-off"]')
        check("comparison: the 'on' run says excerpts were sent", "excerpt" in on.lower(), on[:120])
        check("comparison: the 'off' run says the whole document was sent", "whole document" in off, off[:120])
        check("comparison: both answers are there", "Not run" not in on + off)
        page.screenshot(path=f"{SHOTS}-kb-step5.png", full_page=True)

        # 4. Removing the document deletes its collection; step 3 is empty again.
        page.get_by_role("button", name="2. Attach Document").click()
        page.get_by_role("button", name="Remove").click()
        page.wait_for_selector("input[type=file]", timeout=20000)
        check("remove: the document's collection is gone from the kb-server",
              not (mine & student_collections()))
        page.get_by_role("button", name="3. Connect Knowledge Base").click()
        page.wait_for_selector('[data-testid="kb-none"]', timeout=20000)
        check("remove: step 3 has nothing to search", True)
        browser.close()

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
