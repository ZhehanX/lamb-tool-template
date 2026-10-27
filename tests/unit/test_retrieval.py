"""Step 3: the student's step-2 document searched as a knowledge base (plan
Phase 8, T15), with the kb-server and LAMB replaced by fakes."""

import pytest

from app.workshop import kb_client, retention, service, store
from app.workshop.ports import Chunk
from conftest import frames_of, launch  # noqa: E402

DOCUMENT = "# Aigua\n\nEs condensa en núvols."
CANDIDATES = [
    ("Es condensa en núvols.", 0.81, "aigua.md"),
    ("Cau com a pluja.", 0.70, "aigua.md"),
    ("Torna al mar.", 0.55, "aigua.md"),
    ("Els volcans.", -0.05, "aigua.md"),
]


class FakeRetriever:
    def __init__(self):
        self.queries = []
        self.last = {}

    def search(self, query, k):
        self.queries.append((query, k))
        self.last = {"query": query, "k": k, "candidate_count": len(CANDIDATES),
                     "timing": {"total_ms": 12}, "elapsed_ms": 14,
                     "embedding": {"vendor": "ollama", "model": "bge-m3"},
                     "chunk_size": 1000, "chunk_overlap": 200}
        return [Chunk(t, s, src, i + 1, i < k, {"chunk_index": i, "chunk_count": 4})
                for i, (t, s, src) in enumerate(CANDIDATES)]


@pytest.fixture
def kb(monkeypatch):
    fake = FakeRetriever()
    monkeypatch.setattr(service, "retriever", lambda uid, rlid, guid: fake)
    return fake


@pytest.fixture
def kb_server(monkeypatch):
    """The kb-server calls the tool makes. Every file reports `status`."""
    s = {"collections": {}, "deleted": [], "status": "processing", "chunks": 0, "down": False}
    monkeypatch.setattr(kb_client, "configured", lambda: True)

    def create(name, owner, **kw):
        if s["down"]:
            raise kb_client.KbError("down")
        cid = 70 + len(s["collections"]) + len(s["deleted"])
        s["collections"][cid] = {"name": name, "owner": owner, "texts": []}
        return cid

    def delete(cid):
        if s["down"]:
            raise kb_client.KbError("down")
        s["deleted"].append(cid)
        return s["collections"].pop(cid, None) is not None

    monkeypatch.setattr(kb_client, "create_collection", create)
    monkeypatch.setattr(kb_client, "delete_collection", delete)
    monkeypatch.setattr(kb_client, "get_collection",
                        lambda cid: {"embeddings_model": {"vendor": "ollama", "model": "bge-m3"}})
    monkeypatch.setattr(kb_client, "ingest_text", lambda cid, name, text: (
        s["collections"][cid]["texts"].append((name, text)) or {"file_registry_id": 1}))
    monkeypatch.setattr(kb_client, "list_files", lambda cid: [
        {"original_filename": n, "status": s["status"], "document_count": s["chunks"]}
        for n, _ in s["collections"].get(cid, {"texts": []})["texts"]])
    return s


def upload(client, headers, name="aigua.md", text=DOCUMENT):
    return client.post("/workshop/api/document", headers=headers,
                       files={"file": (name, text.encode("utf-8"), "text/markdown")})


def chat(client, headers, message="Què passa amb el vapor?", rag_mode="on"):
    return frames_of(client.post("/workshop/api/chat", headers=headers,
                                 json={"message": message, "rag_mode": rag_mode}))


# --- the student's document becomes their knowledge base ------------------------

def test_without_a_kb_server_step_3_is_unavailable(client):
    headers, _ = launch()
    upload(client, headers)
    assert client.get("/workshop/api/state", headers=headers).json()["kb"] == {"status": "unavailable"}


def test_without_a_document_step_3_is_empty(client, kb_server):
    headers, _ = launch()
    assert client.get("/workshop/api/state", headers=headers).json()["kb"] == {"status": "none"}
    r = client.post("/workshop/api/probe", headers=headers, json={"query": "x"})
    assert (r.status_code, r.json()["code"]) == (409, "not_ready")
    assert "Attach a document in step 2" in r.json()["detail"]


def test_the_step_2_document_is_what_step_3_searches(client, kb_server):
    headers, uid = launch()
    r = upload(client, headers, "aigua.pdf.md", "# Aigua\r\n\r\nEs condensa.  ")
    assert r.json()["kb"]["status"] == "processing"
    [(cid, collection)] = kb_server["collections"].items()
    assert collection["owner"] == f"taller-ia:lms.test:link-1:{uid}"
    assert collection["texts"] == [("aigua.pdf.md", "# Aigua\n\nEs condensa.")], \
        "the text step 2 extracted, not the file"
    r = client.post("/workshop/api/probe", headers=headers, json={"query": "x"})
    assert "still being prepared" in r.json()["detail"]

    kb_server["status"], kb_server["chunks"] = "completed", 2
    assert client.get("/workshop/api/kb", headers=headers).json() == {
        "status": "ready", "chunks": 2, "chunk_size": 1000, "chunk_overlap": 200}
    assert service.retriever(uid, "link-1", "lms.test").collection_id == cid


def test_each_student_searches_only_their_own_document(client, kb_server):
    a, uid_a = launch("A", "u1")
    b, uid_b = launch("B", "u2")
    upload(client, a, "a.md", "Primer.")
    upload(client, b, "b.md", "Segon.")
    kb_server["status"], kb_server["chunks"] = "completed", 1
    owners = {c["owner"]: cid for cid, c in kb_server["collections"].items()}
    assert service.retriever(uid_a, "link-1", "lms.test").collection_id == owners[f"taller-ia:lms.test:link-1:{uid_a}"]
    assert service.retriever(uid_b, "link-1", "lms.test").collection_id == owners[f"taller-ia:lms.test:link-1:{uid_b}"]


def test_replacing_or_removing_the_document_deletes_its_collection(client, kb_server):
    headers, uid = launch()
    upload(client, headers, "primer.md", "Primer.")
    client.put("/workshop/api/assistant", headers=headers, json={"retrieval_enabled": True})
    upload(client, headers, "segon.md", "Segon.")
    [(second, collection)] = kb_server["collections"].items()
    assert collection["texts"] == [("segon.md", "Segon.")] and kb_server["deleted"] == [70]
    state = client.get("/workshop/api/state", headers=headers).json()
    assert state["assistant"]["retrieval_enabled"] is False, "a new document starts out whole"

    r = client.delete("/workshop/api/document", headers=headers).json()
    assert r["document"] is None and r["kb"] == {"status": "none"}
    assert kb_server["deleted"] == [70, second] and kb_server["collections"] == {}
    assert store.student_collection(uid, "link-1", "lms.test") is None and store.kb_trash() == []


def test_a_removal_the_kb_server_misses_happens_later(client, kb_server):
    headers, uid = launch()
    upload(client, headers)
    kb_server["down"] = True
    client.delete("/workshop/api/document", headers=headers)
    assert store.student_collection(uid, "link-1", "lms.test") is None, "nothing searches it any more"
    assert store.kb_trash() == [70] and retention.empty_kb_trash() == 0
    kb_server["down"] = False
    assert retention.empty_kb_trash() == 1
    assert store.kb_trash() == [] and kb_server["collections"] == {}


def test_if_indexing_fails_the_document_still_works_whole(client, kb_server, lamb):
    headers, _ = launch()
    kb_server["down"] = True
    r = upload(client, headers)
    assert r.status_code == 200 and r.json()["document"]["name"] == "aigua.md"
    assert r.json()["kb"] == {"status": "failed"}
    lamb["replies"].append(["D'acord."])
    chat(client, headers, rag_mode="on")
    assert DOCUMENT in lamb["requests"][0]["messages"][0]["content"]


# --- searching it -----------------------------------------------------------------

def test_probe_returns_every_candidate_and_is_traced(client, lamb, kb):
    headers, uid = launch()
    body = client.post("/workshop/api/probe", headers=headers, json={"query": "vapor"}).json()
    assert [r["selected"] for r in body["results"]] == [True, True, True, False]
    assert body["results"][3] == {"rank": 4, "similarity": -0.05, "data": "Els volcans.",
                                  "source": "aigua.md", "selected": False,
                                  "meta": {"chunk_index": 3, "chunk_count": 4}}
    assert (body["k"], body["chunk_size"], body["embedding"]["model"]) == (3, 1000, "bge-m3")
    assert lamb["requests"] == [], "no model call"
    with service.store.get_conn() as conn:
        kinds = [r["kind"] for r in conn.execute("SELECT kind FROM traces WHERE user_id = ?", (uid,))]
    assert kinds == ["probe"]


def connected(client, tools=()):
    headers, _ = launch()
    client.put("/workshop/api/assistant", headers=headers, json={
        "instructions": "Respon en català.", "retrieval_enabled": True, "tools": list(tools)})
    upload(client, headers)
    # The upload starts the document out whole: connect it again.
    client.put("/workshop/api/assistant", headers=headers, json={"retrieval_enabled": True})
    return headers


def test_on_the_excerpts_replace_the_whole_document(client, lamb, kb):
    headers = connected(client)
    lamb["replies"].append(["Es condensa."])
    out = chat(client, headers)

    system, question = (m["content"] for m in lamb["requests"][0]["messages"])
    assert system == "Respon en català.", "the document is not sent whole"
    assert question.startswith("Excerpts from the document:\n\n[1] aigua.md\nEs condensa en núvols.")
    assert "Torna al mar." in question and "Els volcans." not in question
    assert question.endswith("\n\nQuestion:\nQuè passa amb el vapor?")
    assert kb.queries == [("Què passa amb el vapor?", 3)]

    request = out[0]["data"]
    assert request["rag_mode"] == "on"
    assert [c["selected"] for c in request["retrieval"]["candidates"]] == [True, True, True, False]
    assert request["retrieval"]["query"] == "Què passa amb el vapor?"
    assert [s["document_id"] for s in request["retrieved_sources"]] == ["aigua.md"] * 3
    turn = client.get("/workshop/api/state", headers=headers).json()["turns"][0]
    assert (turn["rag_mode"], turn["retrieved"]) == ("on", 3)


def test_off_the_whole_document_and_no_search_at_all(client, lamb, kb):
    headers = connected(client, tools=["kb_search"])
    lamb["replies"].append(["No ho sé."])
    chat(client, headers, rag_mode="off")
    [system, user] = lamb["requests"][0]["messages"]
    assert DOCUMENT in system["content"] and "kb_search" not in system["content"]
    assert user == {"role": "user", "content": "Què passa amb el vapor?"}
    assert kb.queries == []
    turn = client.get("/workshop/api/state", headers=headers).json()["turns"][0]
    assert (turn["rag_mode"], turn["retrieved"]) == ("off", 0)


def test_the_preview_follows_the_switch(client, kb):
    headers = connected(client)
    def system(mode):
        frame = client.post("/workshop/api/preview", headers=headers,
                            json={"message": "x", "rag_mode": mode}).json()
        return frame["data"]["rag_mode"], frame["data"]["request_body"]["messages"][0]["content"]
    assert system("on") == ("on", "Respon en català.")
    mode, text = system("off")
    assert mode == "off" and DOCUMENT in text


def test_a_student_who_did_not_connect_it_gets_the_whole_document(client, lamb, kb):
    headers, _ = launch()
    upload(client, headers)
    lamb["replies"].append(["Hola."])
    chat(client, headers, rag_mode="on")
    assert kb.queries == []
    assert DOCUMENT in lamb["requests"][0]["messages"][0]["content"]
    assert lamb["requests"][0]["messages"][-1]["content"] == "Què passa amb el vapor?"


def test_the_kb_search_tool_without_connecting(client, lamb, kb):
    headers, _ = launch()
    client.put("/workshop/api/assistant", headers=headers, json={"tools": ["kb_search"]})
    lamb["replies"] += [['<tool name="kb_search">vapor</tool>'], ["Es condensa."]]
    out = chat(client, headers, rag_mode="off")
    assert "- kb_search: Searches the attached document" in lamb["requests"][0]["messages"][0]["content"]
    [done] = [f["data"] for f in out if f != "[DONE]" and f.get("type") == "tool_event"
              and f["data"]["type"] == "tool_done"]
    assert done["success"] and done["result"].startswith("[1] aigua.md\nEs condensa en núvols.")
    assert "Els volcans." not in done["result"]
    assert kb.queries == [("vapor", 3)]


def test_there_is_no_course_knowledge_base_page_any_more(client):
    teacher, _ = launch("Teacher", "t1", instructor=True)
    assert client.get("/course-kb", headers=teacher).status_code == 404
