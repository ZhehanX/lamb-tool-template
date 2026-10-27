"""Contract: the parts of the kb-server REST API the tool relies on (plan
Phase 8). The API is not a published contract, so this pins it; run it
before changing the kb-server image.

Run (needs the tool's kb-server, see docker-compose.dev.yaml):
  KB_CONTRACT=1 KB_SERVER_URL=http://127.0.0.1:9190 KB_SERVER_TOKEN=... pytest tests/contract
"""

import os
import time
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("KB_CONTRACT") != "1", reason="set KB_CONTRACT=1 to run against a live kb-server")

TEXT = """# El cicle de l'aigua

L'aigua s'evapora dels oceans i dels rius quan el sol l'escalfa.

El vapor puja, es refreda i es condensa en núvols.

Quan les gotes pesen prou, cauen com a pluja o neu: és la precipitació.

L'aigua que cau s'escola cap als rius o s'infiltra al subsòl, i torna al mar.
""" * 6


@pytest.fixture(scope="module")
def kb():
    from app.workshop import kb_client
    if not kb_client.configured():
        pytest.fail("KB_SERVER_URL and KB_SERVER_TOKEN are required")
    return kb_client


@pytest.fixture(scope="module")
def collection(kb):
    cid = kb.create_collection(f"contract-{uuid.uuid4().hex[:8]}", "taller-ia:contract")
    yield cid
    kb.delete_collection(cid)


def test_ingest_query_delete(kb, collection):
    started = kb.ingest_file(collection, "aigua.md", TEXT.encode(), "text/markdown")
    job = started["file_registry_id"]
    assert started["success"] and job

    for _ in range(120):
        status = kb.ingestion_job(collection, job)["status"]
        if status in ("completed", "failed"):
            break
        time.sleep(1)
    assert status == "completed"

    [entry] = kb.list_files(collection)
    assert entry["original_filename"] == "aigua.md" and entry["document_count"] > 1
    assert entry["plugin_params"]["chunk_size"] == kb.CHUNK_SIZE

    body = kb.query(collection, "Què passa quan el vapor es refreda?", top_k=5)
    assert body["count"] == len(body["results"]) > 0 and "timing" in body
    for r in body["results"]:
        assert -1.0 <= r["similarity"] <= 1.0
        meta = r["metadata"]
        assert {"source", "chunk_index", "chunk_count", "chunk_size", "chunk_overlap",
                "embedding_model", "embedding_vendor"} <= set(meta)
        # A chunk names its file by the stored path, not the uploaded name.
        assert meta["source"] == entry["file_path"]
    scores = [r["similarity"] for r in body["results"]]
    assert scores == sorted(scores, reverse=True)

    retriever = kb.KbServerRetriever(collection, candidates=5)
    chunks = retriever.search("Què passa quan el vapor es refreda?", k=2)
    assert [c.selected for c in chunks] == [True, True] + [False] * (len(chunks) - 2)
    assert {c.source for c in chunks} == {"aigua.md"}, "the uploaded name, from the registry"
    assert all("source" not in c.meta and "file_url" not in c.meta for c in chunks), \
        "server paths and /static links stay in the tool"
    assert retriever.last["candidate_count"] == len(chunks)
    assert retriever.last["embedding"]["vendor"] == "ollama" and retriever.last["embedding"]["model"]


def test_extracted_text_under_its_original_name(kb):
    """Step 2's text, not its file (T15): simple_ingest takes text whatever
    the extension, and the excerpts are named after the student's file."""
    cid = kb.create_collection(f"contract-{uuid.uuid4().hex[:8]}", "taller-ia:contract")
    try:
        job = kb.ingest_text(cid, "apunts.pdf", TEXT)["file_registry_id"]
        for _ in range(120):
            status = kb.ingestion_job(cid, job)["status"]
            if status in ("completed", "failed"):
                break
            time.sleep(1)
        assert status == "completed"
        [entry] = kb.list_files(cid)
        assert entry["plugin_name"] == "simple_ingest" and entry["document_count"] > 1
        chunks = kb.KbServerRetriever(cid).search("Què passa quan el vapor es refreda?", k=3)
        assert {c.source for c in chunks} == {"apunts.pdf"}
        assert any("es condensa en núvols" in c.text for c in chunks if c.selected)
    finally:
        kb.delete_collection(cid)


def test_a_deleted_collection_is_gone(kb):
    cid = kb.create_collection(f"contract-{uuid.uuid4().hex[:8]}", "taller-ia:contract")
    assert kb.delete_collection(cid) is True
    assert kb.get_collection(cid) is None
    assert kb.delete_collection(cid) is False
    with pytest.raises(kb.KbError) as e:
        kb.query(cid, "x")
    assert e.value.status == 404


def test_collection_names_are_global_so_a_rerun_finds_the_existing_one(kb):
    name = f"contract-{uuid.uuid4().hex[:8]}"
    first = kb.create_collection(name, "taller-ia:contract")
    try:
        assert kb.create_collection(name, "taller-ia:contract") == first
        # So each student's document gets a name of its own, a new one per upload.
        assert kb.student_collection_name("lms-a", "1", 7) != kb.student_collection_name("lms-b", "1", 7)
        assert kb.student_collection_name("lms-a", "1", 7) != kb.student_collection_name("lms-a", "1", 7)
    finally:
        kb.delete_collection(first)
