"""Client for the tool's own lamb-kb-server instance (plan 8C, T8-T10).

Ported from the old branch's `lamb/modules/workshop/kb.py`, keeping only the
calls it already made straight to the kb-server REST API (collection create
was its fallback path; the main path went through LAMB's KBServerManager,
which a standalone tool does not have).

The kb-server has one shared bearer token and no per-user access control,
and serves uploaded originals under /static without authentication (T9).
So: only the tool talks to it, over an internal address; a collection id
only ever comes from the tool's database (`kb_collections`), never from a
request; and the `file_url` / `source` paths in chunk metadata are not
passed on to browsers.

Similarity is 1 - cosine distance, between -1 and 1 in theory. The query
threshold is -1 so the panel can show every candidate, low scores included."""

import hashlib
import json
import os
import time
import uuid

import httpx

from .ports import Chunk

KB_SERVER_URL = os.getenv("KB_SERVER_URL", "").rstrip("/")
KB_SERVER_TOKEN = os.getenv("KB_SERVER_TOKEN", "")

# Chunking of the student's document: the old branch's values, shown to students.
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
# ingest_file: plain text through simple_ingest, anything else through
# markitdown first. ingest_text always sends text to simple_ingest.
TEXT_PLUGIN = "simple_ingest"
TEXT_EXTENSIONS = (".txt", ".md", ".markdown", ".html", ".htm")
CONVERT_PLUGIN = "markitdown_ingest"

_TRANSPORT = None  # tests swap in an httpx.MockTransport


class KbError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, detail: str = ""):
        super().__init__(message)
        self.status = status
        self.detail = detail


def configured() -> bool:
    return bool(KB_SERVER_URL and KB_SERVER_TOKEN)


def _client(timeout: float = 30) -> httpx.Client:
    if not configured():
        raise KbError("The knowledge base server is not configured (KB_SERVER_URL / KB_SERVER_TOKEN).")
    return httpx.Client(base_url=KB_SERVER_URL, timeout=timeout, transport=_TRANSPORT,
                        headers={"Authorization": f"Bearer {KB_SERVER_TOKEN}"})


def _check(resp: httpx.Response, what: str, ok=(200,)):
    if resp.status_code not in ok:
        raise KbError(f"Knowledge base server: {what} failed ({resp.status_code}).",
                      status=resp.status_code, detail=resp.text[:2000])
    return resp.json() if resp.content else None


def _call(method: str, path: str, what: str, ok=(200,), timeout: float = 30, **kwargs):
    try:
        with _client(timeout) as c:
            return _check(c.request(method, path, **kwargs), what, ok)
    except httpx.HTTPError as e:
        raise KbError("The knowledge base server cannot be reached.", detail=str(e)) from e


# --- collections -------------------------------------------------------------------

def student_collection_name(consumer_guid: str, resource_link_id: str, user_id: int) -> str:
    """Collection names are global in the kb-server (ChromaDB). Each student
    of each activity gets one per attached document, so a replaced document
    never finds the old one's chunks under the same name."""
    digest = hashlib.sha1(f"{consumer_guid}\n{resource_link_id}\n{user_id}".encode()).hexdigest()[:24]
    return f"student-{digest}-{uuid.uuid4().hex[:8]}"


def find_collection(name: str) -> int | None:
    body = _call("GET", "/collections", "listing collections", params={"limit": 1000})
    for item in (body or {}).get("items", []):
        if item.get("name") == name:
            return int(item["id"])
    return None


def create_collection(name: str, owner: str, embeddings_model: dict | None = None,
                      description: str = "taller-ia") -> int:
    """embeddings_model None uses the server's EMBEDDINGS_* environment
    ("default"); a dict like {"vendor": "ollama", "model": "bge-m3",
    "api_endpoint": ..., "apikey": ""} pins another model for this collection."""
    model = embeddings_model or {"model": "default", "vendor": "default",
                                 "api_endpoint": "default", "apikey": "default"}
    try:
        body = _call("POST", "/collections", "creating a collection", ok=(200, 201), json={
            "name": name, "description": description, "owner": owner,
            "visibility": "private", "embeddings_model": model})
    except KbError as e:
        # Created before but not recorded (e.g. the tool stopped in between).
        if e.status == 409 and (existing := find_collection(name)) is not None:
            return existing
        raise
    return int(body["id"])


def get_collection(collection_id: int) -> dict | None:
    try:
        return _call("GET", f"/collections/{collection_id}", "reading a collection")
    except KbError as e:
        if e.status == 404:
            return None
        raise


def delete_collection(collection_id: int) -> bool:
    """Deletes the vectors, the stored files and the records. False if it was
    already gone."""
    try:
        _call("DELETE", f"/collections/{collection_id}", "deleting a collection",
              ok=(200, 204), timeout=60)
        return True
    except KbError as e:
        if e.status == 404:
            return False
        raise


# --- files -------------------------------------------------------------------------

def ingest_file(collection_id: int, filename: str, content: bytes,
                content_type: str = "application/octet-stream") -> dict:
    """Upload and start the (asynchronous) ingestion. Returns the
    AddDocumentsResponse: `file_registry_id` doubles as the job id."""
    plugin = TEXT_PLUGIN if filename.lower().endswith(TEXT_EXTENSIONS) else CONVERT_PLUGIN
    return _ingest(collection_id, filename, content, content_type, plugin)


def ingest_text(collection_id: int, filename: str, text: str) -> dict:
    """Ingest text the tool already extracted (step 2's document), so the
    excerpts come from exactly the text step 2 places in the context.
    simple_ingest reads any upload as UTF-8 and does not check the extension;
    the original filename only names the excerpts' source."""
    return _ingest(collection_id, filename, text.encode("utf-8"), "text/plain", TEXT_PLUGIN)


def _ingest(collection_id: int, filename: str, content: bytes, content_type: str,
            plugin: str) -> dict:
    params = {"chunk_size": CHUNK_SIZE, "chunk_overlap": CHUNK_OVERLAP}
    return _call("POST", f"/collections/{collection_id}/ingest-file", "uploading a file",
                 ok=(200, 201, 202), timeout=300,
                 files={"file": (filename, content, content_type)},
                 data={"plugin_name": plugin, "plugin_params": json.dumps(params)})


def ingestion_job(collection_id: int, job_id: int) -> dict:
    return _call("GET", f"/collections/{collection_id}/ingestion-jobs/{job_id}",
                 "reading an ingestion job")


def list_files(collection_id: int) -> list[dict]:
    body = _call("GET", f"/collections/{collection_id}/files", "listing files")
    return body if isinstance(body, list) else (body or {}).get("items", [])


def delete_file(collection_id: int, file_id: int) -> None:
    _call("DELETE", f"/collections/{collection_id}/files/{file_id}", "deleting a file",
          ok=(200, 204), timeout=60)


# --- query -------------------------------------------------------------------------

def query(collection_id: int, text: str, top_k: int = 10, threshold: float = -1.0) -> dict:
    """{results: [{similarity, data, metadata}], count, timing, query}"""
    return _call("POST", f"/collections/{collection_id}/query", "searching", timeout=60,
                 params={"plugin_name": "simple_query"},
                 json={"query_text": text, "top_k": top_k, "threshold": threshold,
                       "plugin_params": {}})


def public_metadata(meta: dict) -> dict:
    """What of a chunk's metadata may reach the browser: no server paths and
    no /static links (T9). `filename` there is the stored (random) name; the
    original one comes from the file registry."""
    keep = ("chunk_index", "chunk_count", "chunking_strategy", "chunk_size",
            "chunk_overlap", "embedding_model", "embedding_vendor")
    return {k: meta[k] for k in keep if k in meta}


class KbServerRetriever:
    """Retriever over one kb-server collection (plan T5).

    Fetches `candidates` results so the panel can show what was NOT selected
    and where the top-k line fell. `last` describes the latest search for
    the trace: query, timing, candidates count, chunking and embedding."""

    def __init__(self, collection_id: int, candidates: int = 10):
        self.collection_id = collection_id
        self.candidates = candidates
        self.last: dict = {}

    def search(self, query_text: str, k: int) -> list[Chunk]:
        started = time.monotonic()
        body = query(self.collection_id, query_text, top_k=max(self.candidates, k))
        results = sorted(body.get("results", []), key=lambda r: r.get("similarity", 0), reverse=True)
        # Chunks name their file by its stored path; the registry knows the
        # name it was uploaded under.
        names = {f.get("file_path"): f.get("original_filename") for f in list_files(self.collection_id)}
        chunks = []
        for rank, r in enumerate(results, 1):
            raw = r.get("metadata") or {}
            source = names.get(raw.get("source")) or "document"
            chunks.append(Chunk(text=r.get("data", ""), score=float(r.get("similarity", 0)),
                                source=source, rank=rank, selected=rank <= k,
                                meta=public_metadata(raw)))
        first = chunks[0].meta if chunks else {}
        self.last = {"query": query_text, "k": k, "candidate_count": len(chunks),
                     "timing": body.get("timing", {}),
                     "elapsed_ms": round((time.monotonic() - started) * 1000),
                     "embedding": {"vendor": first.get("embedding_vendor"),
                                   "model": first.get("embedding_model")},
                     "chunk_size": first.get("chunk_size", CHUNK_SIZE),
                     "chunk_overlap": first.get("chunk_overlap", CHUNK_OVERLAP)}
        return chunks
