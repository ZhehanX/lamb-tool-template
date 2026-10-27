# taller-ia

The AI Workshop as a standalone LTI 1.1 tool built on
[lamb-tool-template](https://github.com/Lamb-Project/lamb-tool-template).
Students build their own assistant in a guided wizard, and an observability
panel shows exactly what the model receives. LAMB is used only as a
transparent engine: one published assistant with an empty system prompt, an
empty prompt template and `no_rag`, reached with a personal creator API key.
The tool assembles the whole context itself.

### Version pairing

The tool relies on LAMB behaviour that is not a published contract (an empty
assistant passes messages through unchanged), so versions are pinned and
`LAMB_CONTRACT=1` tests guard the pairing.

| Component | Version |
|---|---|
| lamb-tool-template | `4ad73dc` |
| LAMB | `874f5cd` (upstream `dev` = 0.8) |
| lamb-kb-server (Phase 8) | `ghcr.io/lamb-project/lamb-kb:v0.7@sha256:5773572f3365a317aaa38fae5831f7d53464979fc5ed129e8c8949d373a25324` (same `lamb-kb-server-stable/` tree as `874f5cd`) |
| mock LMS (lamb-lti-test-tool) | `1cacfac` |

### Development

The whole tool side runs in Docker: `docker-compose.dev.yaml` on top of the
deployment file.

```bash
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml up -d --build
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml up -d --build tool   # after a code change
```

| Service | Where | Notes |
|---|---|---|
| `tool` (this tool, wizard SPA included) | :8090 | SQLite in the `tool-data` volume (WAL is unreliable on a Windows bind mount) |
| `kb` (own lamb-kb-server) | 127.0.0.1:9190 | the tool itself uses `http://kb:9090` |
| `lms` (mock LMS, lamb-lti-test-tool) | internal :8000 | built from `../lamb-lti-test-tool`; its database stays the file in that checkout |
| `lms-gateway` (nginx) | :8001 | the browser's way in to `lms`; sends `Host: lms:8000`, so the grade URL the mock LMS hands the tool is `http://lms:8000/outcomes` |
| LAMB 0.8 | :9099 | a separate checkout of `874f5cd` with its own compose project; the LAMB base on `/admin` is `http://host.docker.internal:9099` |
| Ollama | :11434 | native on the host (GPU), shared with LAMB |

`npm run dev` in `frontend/` still serves the SPA on :5173 against the tool on
:8090. The Docker build installs `requirements.txt` within `constraints.txt`,
the versions the tests last passed with.

`.env` beyond the template's: `KB_SERVER_URL`, `KB_SERVER_TOKEN` (never name
it `LAMB_API_KEY` here: that pre-seeds the LAMB key), and optionally
`WORKSHOP_TOOL_CALL_STYLE` (`tag` | `qwen`) and `WORKSHOP_RETENTION_DAYS`
(automatic deletion is off without it). The engine assistant and its context
window are set on `/admin`.

Tests:

```bash
pytest                                   # unit tests, no services needed
LAMB_CONTRACT=1 LAMB_CONTRACT_KEY=... LAMB_CONTRACT_DEBUG_MODEL=lamb_assistant.<debug id> \
KB_CONTRACT=1 KB_SERVER_URL=... KB_SERVER_TOKEN=... pytest tests/contract
cd frontend && npx vitest run
python tests/e2e_workshop.py             # browser end to end, all services up
python tests/e2e_document_kb.py          # (see each file's docstring for its env)
# the e2e scripts replace the tool's course link in the mock LMS on every run
python scripts/tool_compliance.py        # tool-call format compliance of the engine model
python scripts/compare_embeddings.py DIR # which embedding model finds the right excerpts
```

The rest of this README is the template's own documentation.

---

# lamb-tool-template

A minimal, well-built starting point for an educational tool that:

- is launched from your LMS (Moodle / Atenea) as an **LTI 1.1** activity,
- talks to a **LAMB assistant** as its AI engine, over LAMB's OpenAI-compatible API,
- shows students a **chatbot** and passes **grades** back to the LMS.

Clone it, change the parts you care about, deploy it. Everything is
GPL-3.0; contributors who build on this can later fold their tool back into
the LAMB ecosystem as an extension.

> This is a **template**, not a finished product. The chat UI, the grading
> policy, and the conversation flow are deliberately small and obvious so
> you can replace them. Where the code makes a security decision (LTI
> signature validation, session handling, keeping the LAMB key server-side)
> it is not negotiable — see [SECURITY](#security).

## What you get

| Piece | File | Yours to change? |
|---|---|---|
| Admin console (config + LTI URL) | `app/routers/admin.py` | As you add settings |
| LTI 1.1 launch + OAuth validation | `app/lti/validation.py` | Rarely — it's the security boundary |
| Grade passback (Basic Outcomes) | `app/lti/outcomes.py` | The *policy* yes, the signing no |
| LAMB API client | `app/lamb_client.py` | If you call LAMB differently |
| Student chat | `app/routers/chat.py`, `app/templates/chat.html`, `app/static/chat.js` | **Yes — this is the point** |
| Instructor setup + dashboard | `app/routers/instructor.py`, `app/templates/setup.html`, `app/templates/dashboard.html` | Yes |
| Storage (SQLite, no ORM) | `app/db.py` | As your data grows |

## Quick start

```bash
git clone https://github.com/Lamb-Project/lamb-tool-template
cd lamb-tool-template
cp .env.example .env         # then fill it in — there are no working defaults
docker compose up --build    # tool on http://localhost:8080
```

To run without Docker:

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8080
```

You cannot launch the tool by visiting it in a browser — an LTI tool is
*launched by an LMS*. For local development without a real Moodle, use the
bundled test LMS: see [`docs/dev-harness.md`](docs/dev-harness.md).

## Configuration

Configuration is split in two:

**Boot config — environment (`.env`)**, needed before the tool can serve:

- `ADMIN_USERNAME` / `ADMIN_PASSWORD` — protect the admin console.
- `PUBLIC_BASE_URL` — the URL the LMS uses to reach this tool. Behind a
  reverse proxy this must be the *public* URL, because the OAuth signature
  is computed over it.

**Runtime config — the admin console (`/admin`)**, set after the tool is up:

- **LAMB API base URL + API key** — your LAMB instance and the key this tool
  uses to reach it. The key is held **server-side only** and never reaches
  the browser.
- **LTI consumer key + shared secret** — must match the External Tool
  configuration you create in Moodle/Atenea.

Log in at `/admin` with the credentials from `.env`, fill in those four
values, and the page shows you the **LTI launch URL** to register in your
LMS. Until they're set, the tool safely rejects all launches.

> For Docker/CI you may pre-seed the four runtime values from the
> environment on first boot (see the commented block in
> [`.env.example`](.env.example)); once saved via `/admin`, the stored value
> wins. See [`.env.example`](.env.example) for the full annotated list.

## Deploying to Atenea / Moodle

See [`docs/atenea-setup.md`](docs/atenea-setup.md) for the External Tool
configuration, step by step.

## How it works

One page, launch to grade: [`docs/anatomy.md`](docs/anatomy.md).

## Try it locally

A screenshot walkthrough — set up the tool, register it in a mock LMS, and
run the full launch → chat → grade flow without needing Moodle:
[`docs/testing-with-lti-test-tool.md`](docs/testing-with-lti-test-tool.md).

## Growing this into a LAMB extension

The contract for folding a tool built on this template back into the LAMB
ecosystem: [`docs/EXTENDING.md`](docs/EXTENDING.md).

## Security

This template does the things LTI tools most often get wrong, correctly —
copy the posture, don't weaken it:

- **Inbound launches are verified and rejected on failure.** The OAuth 1.0a
  HMAC-SHA1 signature is recomputed and compared; the timestamp must be
  fresh; the nonce must be unused (replay protection). An unverified launch
  is a forged one.
- **Sessions are server-side and persisted.** A random token in SQLite,
  accepted from cookie / header / query so it survives the LMS iframe. A
  restart does not log anyone out.
- **The LAMB key never leaves the server.** The browser talks to this tool;
  this tool talks to LAMB.
- **No working default secrets.** The app refuses to start until you set
  real ones.

## Running the tests

```bash
# with the tool running on :8080 and its .env loaded
.venv/bin/python tests/test_lti_launch.py
```

The tests act as the LMS (they sign their own launches) and cover the
launch/validation/session paths — valid launch, forged signature, tampered
role, replayed nonce, stale timestamp, missing session. They need neither a
real LMS nor a running LAMB.

## License

LAMB is licensed under the GNU General Public License v3.0 (GPL v3).

Copyright (c) 2024-2026 Marc Alier (UPC) @granludo & Juanan Pereira (UPV/EHU) @juananpe
