"""Phase 8 step 2: which embedding model finds the right course excerpt.

For each model, creates its own collection in the tool's kb-server (the model
is fixed per collection, so nothing else changes), ingests the same course
materials, asks the same questions and checks whether a chunk containing the
expected passage is among the top 3 (what goes into the prompt) and at which
rank it first appears in the top 10. The collections are deleted at the end.

Development only. Needs KB_SERVER_URL / KB_SERVER_TOKEN (the tool's .env) and
the models pulled in the Ollama the kb-server reaches.

Usage:
  python scripts/compare_embeddings.py CORPUS_DIR [--models nomic-embed-text bge-m3]
      [--ollama http://host.docker.internal:11434] [--questions questions.json] [--json out.json]

Questions: a JSON list of {"q": ..., "gold": [substring, ...], "lang": ...};
a chunk is correct if it contains any gold substring. The default set below
was written for the Catalan/Spanish Wikipedia test corpus of 2026-10-22.
"""

import argparse
import json
import pathlib
import sys
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.workshop import kb_client as kb  # noqa: E402

QUESTIONS = [
    {"lang": "ca", "q": "Quants cloroplasts té una cèl·lula vegetal típica?",
     "gold": ["entre 10 i 100 cloroplastidis"]},
    {"lang": "ca", "q": "Quin pigment fan servir les plantes per absorbir la llum?",
     "gold": ["clorofil·la com a principal pigment"]},
    {"lang": "ca", "q": "Quant de temps es queda una molècula d'aigua a l'atmosfera?",
     "gold": ["uns deu dies en l'atmosfera"]},
    {"lang": "ca", "q": "Com es formen les gotes de pluja que cauen dels núvols?",
     "gold": ["Precipitació: Es produeix", "Precipitación:"]},
    {"lang": "ca", "q": "Qui va descriure per primera vegada el nucli de la cèl·lula?",
     "gold": ["Brown descrigué el nucli"]},
    {"lang": "ca", "q": "Què no tenen les cèl·lules procariotes que sí que tenen les eucariotes?",
     "gold": ["manquen de sistemes d'endomembranes"]},
    {"lang": "es", "q": "¿Quién ganó el Premio Nobel por estudiar la fotosíntesis?",
     "gold": ["Nobel"]},
    {"lang": "es", "q": "¿De dónde procede el oxígeno que se libera en la fotosíntesis?",
     "gold": ["provenía del agua", "van Niel"]},
    {"lang": "es→ca", "q": "¿Cuánto tiempo permanece una molécula de agua en la atmósfera?",
     "gold": ["uns deu dies en l'atmosfera"]},
    {"lang": "es→ca", "q": "¿Cuántos cloroplastos tiene una célula vegetal típica?",
     "gold": ["entre 10 i 100 cloroplastidis"]},
]


def ingest_all(cid: int, corpus: pathlib.Path) -> float:
    started = time.time()
    jobs = [kb.ingest_file(cid, f.name, f.read_bytes(), "text/markdown")["file_registry_id"]
            for f in sorted(corpus.glob("*.md"))]
    for job in jobs:
        for _ in range(600):
            status = kb.ingestion_job(cid, job)["status"]
            if status in ("completed", "failed"):
                break
            time.sleep(1)
        if status != "completed":
            raise SystemExit(f"ingestion job {job} ended as {status}")
    return time.time() - started


def evaluate(cid: int, questions: list) -> list:
    out = []
    for item in questions:
        started = time.time()
        results = kb.query(cid, item["q"], top_k=10)["results"]
        ranks = [i for i, r in enumerate(results, 1) if any(g in r["data"] for g in item["gold"])]
        out.append({**item, "first_rank": ranks[0] if ranks else None,
                    "top3": bool(ranks and ranks[0] <= 3),
                    "top_score": round(results[0]["similarity"], 4) if results else None,
                    "ms": round((time.time() - started) * 1000)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", type=pathlib.Path)
    ap.add_argument("--models", nargs="+", default=["nomic-embed-text", "bge-m3"])
    ap.add_argument("--ollama", default="http://host.docker.internal:11434")
    ap.add_argument("--questions", type=pathlib.Path)
    ap.add_argument("--json")
    args = ap.parse_args()
    if not kb.configured():
        raise SystemExit("set KB_SERVER_URL and KB_SERVER_TOKEN")
    questions = json.loads(args.questions.read_text(encoding="utf-8")) if args.questions else QUESTIONS

    report = {}
    for model in args.models:
        cid = kb.create_collection(f"embeddings-{model}-{uuid.uuid4().hex[:6]}", "taller-ia:compare",
                                   embeddings_model={"vendor": "ollama", "model": model,
                                                     "api_endpoint": args.ollama, "apikey": ""})
        try:
            seconds = ingest_all(cid, args.corpus)
            chunks = sum(f["document_count"] for f in kb.list_files(cid))
            results = evaluate(cid, questions)
        finally:
            kb.delete_collection(cid)
        report[model] = {"ingest_seconds": round(seconds, 1), "chunks": chunks, "results": results}
        print(f"\n== {model}: {chunks} chunks, ingested in {seconds:.0f} s")
        for r in results:
            print(f"  [{'Y' if r['top3'] else '-'}] rank={r['first_rank']!s:>4}  top={r['top_score']}"
                  f"  {r['lang']:5}  {r['q']}")
        print(f"  top-3: {sum(r['top3'] for r in results)}/{len(results)}")

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                           encoding="utf-8")


if __name__ == "__main__":
    main()
