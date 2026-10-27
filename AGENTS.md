# AGENTS.md

Guidance for AI coding agents, and for people, working in this repository.
Claude Code reads it through `CLAUDE.md`.

## What this is

taller-ia is the AI Workshop as a standalone LTI 1.1 tool, built on
[lamb-tool-template](https://github.com/Lamb-Project/lamb-tool-template). A
student builds a small assistant in a six-step wizard (instructions,
document, knowledge base, tools, chat, reflection), and an observability
panel shows exactly what the model receives.

Everything follows from one idea: **the tool assembles every message itself,
and LAMB passes it through unchanged**, so what the panel shows is what the
model got. LAMB is used only as a transparent engine. Retrieval runs in the
tool's own lamb-kb-server.

```
LMS ──LTI 1.1──▶ tool: FastAPI + Svelte SPA at /workshop
                  ├─ assembler: messages + trace ──creator key──▶ LAMB engine assistant ──▶ LLM
                  ├─ text tool loop: calculator, kb_search
                  ├─ KbServerRetriever ──internal network──▶ own lamb-kb-server ──▶ embeddings (Ollama)
                  └─ SQLite: users, sessions, assistants, turns, traces, consent, ...
```

## Read before changing code

- Comments cite decision IDs: `T15`, `D3`, `C6`, `plan Phase 8`,
  `plan §0.1`. All of them are defined in [docs/decisions.md](docs/decisions.md).
  Read the entry before changing code that cites one.
- The detailed design is in the module docstrings. Start with
  `app/workshop/assembler.py` and `app/workshop/service.py`.
- The first section of `README.md` is this tool's: version pairing,
  development stack, environment variables, tests. Everything below its
  horizontal rule, plus `docs/anatomy.md`, `docs/dev-harness.md` and
  `docs/EXTENDING.md`, is the **template's** documentation. It describes the
  template's `/chat` page. Students of this tool are sent to `/workshop/`
  instead (`app/routers/lti_launch.py`).

## Map

```
app/
  main.py              routers, SPA mount at /workshop, daily retention task
  config.py            boot config from the environment
  settings_store.py    runtime config set on /admin: LAMB URL + key, LTI credentials,
                       engine_model, engine_context_tokens
  db.py                SQLite schema; numbered migrations (_MIGRATIONS, PRAGMA user_version)
  lamb_client.py       the only code that talks to LAMB; LambError, "[Ollama Error]" detection
  sessions.py, lti/    the template's security core: do not modify (T7)
  routers/
    lti_launch.py      launch → session → /workshop/, /setup, /dashboard or /waiting
    workshop.py        JSON + SSE API for the SPA, /workshop/api/*
    instructor.py      setup (rubric, teacher first, consent note), dashboard, grades
    admin.py           /admin console
    chat.py            the template's /chat: students are not sent here; kept for
                       the template's grade-passback e2e test
  workshop/
    assembler.py       every message + the trace; the context budget (T3, T14, T16)
    service.py         chat turn and tool loop, preview, probe, feedback
    text_tools.py      tool calls parsed out of the reply text (T4)
    tools.py           calculator, kb_search
    kb_client.py       own kb-server client, KbServerRetriever (T8, T9, T15)
    ports.py           the replaceable interfaces (T5)
    tokens.py          token estimates
    store.py           workshop data access
    evaluation.py      rubric-based formative feedback (step 6)
    retention.py       opt-in deletion, teacher purge, kb_trash retries
    documents.py       upload → text (markitdown)
    frames.py          SSE frames for the panel
frontend/              SvelteKit + adapter-static, base path /workshop (T6)
  src/routes/+page.svelte        the wizard page
  src/lib/components/            WizardSteps, ObservabilityPanel, RagComparison, ...
  src/lib/services/              workshopApi.js (fetch), workshopChatService.js (SSE)
  src/lib/locales/               en, es, ca, eu
tests/unit/            pytest, needs no services
tests/contract/        pins LAMB and kb-server behaviour; opt-in through env vars
tests/e2e_*.py, tests/test_*.py
                       live-server scripts, run with python (pytest does not collect them)
scripts/               development measurements: tool_compliance, compare_embeddings,
                       calibrate_tokens
```

## Run and test

```bash
# Development stack: tool :8090, kb 127.0.0.1:9190, mock LMS :8001.
# LAMB (:9099, a separate checkout) and Ollama (:11434, on the host) run separately.
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml up -d --build
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml up -d --build tool  # after a code change

.venv/Scripts/python -m pytest       # backend unit tests (.venv/bin/python off Windows)
cd frontend && npx vitest run        # frontend unit tests
cd frontend && npm run build         # what the tool serves at /workshop
cd frontend && npm run check         # svelte-check

# Contract tests, against a running LAMB / kb-server:
LAMB_CONTRACT=1 LAMB_CONTRACT_KEY=... LAMB_CONTRACT_DEBUG_MODEL=lamb_assistant.<debug id> pytest tests/contract
KB_CONTRACT=1 KB_SERVER_URL=http://127.0.0.1:9190 KB_SERVER_TOKEN=... pytest tests/contract

# Browser end to end, everything up (each file's docstring lists its env vars):
python tests/e2e_workshop.py
python tests/e2e_document_kb.py
python tests/e2e_full.py
python tests/test_lti_launch.py
```

What to run:
- Any backend change: `pytest`. Any frontend change: `vitest` and `npm run build`.
- A change to what is sent to LAMB (assembler, `lamb_client`, `service`):
  also `tests/e2e_workshop.py` with `E2E_ENGINE` set to the engine's debug
  twin. That run checks that the messages LAMB received equal the ones the
  panel showed.
- A new LAMB version: the LAMB contract test. A new kb-server image: the KB
  contract test.

## Do not break

1. **The panel shows what was sent.** For every message, its trace segments
   joined equal its content. The panel's `request_body` is the payload as
   sent. Text the tool adds is a `scaffold` segment. (T3)
2. **LAMB receives only `model`, `messages`, `stream`**, and each message
   only `role` + `content`. LAMB drops anything else, `tools` included, so
   tool calls stay text the model writes. (T4)
3. **The engine stays blank**: empty system prompt, empty template, `no_rag`.
   It is set on `/admin`, never chosen by teachers. (T1, T13)
4. **kb-server access goes only through the tool.** A collection id comes
   from the database, never from a request. kb-server file links never
   reach a browser. In production the kb-server publishes no port. (T9)
5. **`[Ollama Error] …` is an error**, even though it arrives as a normal
   HTTP 200 answer. A failed turn is stored as failed and never becomes
   history.
6. **Every workshop row is scoped** by `(user_id, resource_link_id, consumer_guid)`.
7. **Consent comes first.** Routes that store a student's work or send it to
   the model return 403 until the current consent version is accepted. Bump
   `service.CONSENT_VERSION` when what the notice tells students changes
   materially. (C8)
8. **The SPA authenticates with `X-Session-Token` only**, with
   `credentials: 'omit'`. The template reads the cookie first, and a later
   launch in the same browser overwrites it.
9. **Formative feedback never reaches the gradebook.** The teacher sets grades.
10. **The template's security core stays untouched**: `app/lti/*` and
    `app/sessions.py`. The LAMB key and the LTI secret never reach the
    browser. (T7)
11. **The budget order holds**: earlier tool results, then the document, then
    the oldest exchanges, then `ContextOverflow`. (T14, T16)

## Conventions

- Schema changes: add the next numbered entry to `_MIGRATIONS` in
  `app/db.py`. Never edit an entry that has already run; the development
  database is at migration 5.
- UI strings: add each key to all four locale files. Components also pass
  an English `default`.
- Prompt plumbing text (headings, separators, notes) is English and lives
  in `assembler.LABELS`.
- Python dependencies: ranges in `requirements.txt`, the exact versions the
  tests last passed with in `constraints.txt`. Bump them together, after a
  test run.
- Comments explain *why* and cite the decision ID. Code and docs are in English.
- **Record decisions.** When you make a choice a later reader could
  reasonably make differently (what is sent, kept or dropped, an external
  quirk worked around), add an entry to `docs/decisions.md` in the same
  change and cite its ID in the code. A verified fact about LAMB, the
  kb-server or Ollama goes under "External facts" with its version and date.

## Environment gotchas

- The development machine runs Windows. The tool's SQLite uses WAL mode,
  which is unreliable on a Windows bind mount, so in development it lives in
  the named volume `tool-data`.
- In development the tool is on 8090, because LAMB's Open WebUI holds 8080.
  The kb-server is on `127.0.0.1:9190` for host-side tests; the tool itself
  uses `http://kb:9090`.
- From the tool container, the LAMB base URL on `/admin` is
  `http://host.docker.internal:9099`.
- `KB_SERVER_TOKEN` reaches the kb container as `LAMB_API_KEY`. Never put
  `LAMB_API_KEY` in the tool's `.env`: there it pre-seeds the LAMB key.
- Versions are pinned (README, "Version pairing"). Pull the kb-server by
  digest, never `:latest`, and do not build it from source.
- Tests change shared state:
  - the e2e scripts replace the tool's course link in the mock LMS on every
    run (`../lamb-lti-test-tool/lti_platform.db`);
  - `tests/test_admin.py` rewrites the `/admin` settings (LAMB base
    `http://localhost:9099`, consumer key `lamb`), so restore them afterwards;
  - `tests/e2e_workshop.py` with `E2E_ENGINE` switches the engine to the
    debug twin and switches it back at the end. After an interrupted run,
    check `/admin`: with the twin set, students only get their messages echoed.
- The development model `qwen3.5:9b` thinks before it answers: 3–6 s for a
  short question, and sometimes over 120 s on an open one, when LAMB times
  out. That is the occasional failure of `tests/e2e_full.py`.
- Set `OLLAMA_CONTEXT_LENGTH=32768` on the host (Ollama's default is 4096),
  and keep `engine_context_tokens` on `/admin` in line with it.
- Token counts are estimates (LAMB reports no usage for Ollama). The tiktoken
  file is baked into the Docker image; on the host it downloads on first use.

## Known gaps (2026-10-24)

- `activity_settings.steps_enabled` is stored, but nothing reads it.
- Step 2's "Next" requires a document. A student can skip ahead with the
  step bar, and then step 3 has nothing to search.
- `npm run check` reports errors in two test files copied from the old
  branch. The source files are clean.
- No CI.
- Deployment and the pilot (Phase 9) wait on open questions. See
  [docs/decisions.md](docs/decisions.md#open-questions).
