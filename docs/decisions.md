# Decisions

Why taller-ia is built the way it is, and the facts about LAMB, the
kb-server and Ollama that those choices rest on. Code comments cite the IDs
used here; read the entry before changing code that cites it.

| ID in the code | Where it is here |
|---|---|
| `T1`–`T16` | [This tool's decisions](#this-tools-decisions) |
| `8A`, `8B`, `8C` | The retrieval options, under [T8](#t8--retrieval-runs-in-the-tools-own-lamb-kb-server) |
| `D1`–`D7`, `C6`–`C20` | [Carried over from the old branch](#carried-over-from-the-old-branch) |
| `plan §0.1`, `§1`, `§8` | [External facts](#external-facts) |
| `G1`–`G7` | [Goals](#goals) |
| `plan Phase N`, `§9` | [Phase log](#phase-log) |
| `Q2`–`Q10` | [Open questions](#open-questions) |

**Background.** The AI Workshop started as a branch inside LAMB
(`AI-workshop`, PR #515). On 2026-10-20 the supervisor advised against a PR
into all of LAMB, which they could not accept, and suggested an independent
project on lamb-tool-template that connects to a LAMB assistant. That branch
is frozen and is "the old branch" in comments: the panel, wizard, feedback,
retention and calculator code were ported from it.

**Keeping this file true.** When you make a choice a later reader could
reasonably make differently, add an entry in the same change and cite its ID
in the code. When a decision changes, strike the old text through, say what
replaced it and when, and keep the entry: an ID in the code must always
resolve. T16 exists because a choice made in Phase 6 was never written down.

---

## This tool's decisions

### T1 — LAMB is a transparent engine

Every request goes to one published LAMB assistant, the **engine**, with an
empty system prompt, an empty prompt template and `no_rag`. With those
settings LAMB's `simple_augment` passes the messages through unchanged, so
what the tool sends is what the model receives.

This is undocumented LAMB behaviour, not a contract. It is pinned by
`tests/contract/test_lamb_transparency.py` (against a debug twin of the
engine, see [External facts](#lamb)) and by the version pairing in the
README. Re-run the contract test whenever the LAMB version changes.

Setting up an engine on LAMB 0.8: the assistant form pre-fills the
organisation's default system prompt and template, which must be cleared by
hand; hyphens are removed from assistant names (`taller-engine` becomes
`taller_engine`); an assistant created through the API must be published
with a second call.

### T2 — The student's assistant is tool data

The assistant a student builds (instructions, document, tools) is a row in
`student_assistant`, not a LAMB assistant. The creator API key can only list
and call published assistants, not create or delete them.

### T3 — The tool assembles the whole context, with a trace

Instructions, document, excerpts, tool calls and results, and history are
all put together by `app/workshop/assembler.py`, which also returns a trace:
every message split into segments tagged with their source (`instructions`,
`document`, `retrieval`, `tools`, `history`, `user`, `scaffold`). Invariant:
for every message, its segments joined in order equal its content. Text the
tool adds itself (headings, separators, truncation notes) is a `scaffold`
segment and is shown too. The `request_body` the panel shows is the payload
exactly as encoded and sent (`lamb_client.encode_payload`); the panel never
rebuilds it.

Why: this is the panel's data source. It cannot show anything that was not
sent, or hide anything that was. The message layout is in the assembler's
docstring.

### T4 — Tool calls are text the model writes

The system message tells the model how to write a call. The tool parses the
call out of the reply, runs it, appends the call (assistant) and the result
(user), and calls LAMB again, at most `MAX_TOOL_ROUNDS` (3) times. Once a
complete call has been read the LAMB stream is closed: whatever the model
would have invented after it is neither read nor shown. A malformed call is
treated as the final answer (`tool_malformed` event); an unknown tool gets
an error result back.

Why: LAMB's facade forwards no `tools` and has no `tool` role (the contract
test checks this). For the student it is also the lesson: a tool call is
text the model writes.

Two formats (`WORKSHOP_TOOL_CALL_STYLE`):
- `tag` (default): `<tool name="calculator">17*23</tool>`
- `qwen`: `<tool_call>{"name": …, "arguments": …}</tool_call>`, Qwen's own format.

Measured with `scripts/tool_compliance.py` (10 questions, real LAMB,
`qwen3.5:9b`, 2026-10-22): `tag` 10/10, about 13 s per question. `qwen` was
5/10 at first, because the model often stops right after the JSON without
`</tool_call>` (it does this when called directly through Ollama too). After
the parser started accepting an unclosed call that ends the reply it was
10/10, at about 3.3 s per question. The default is still `tag`; see
[Open questions](#open-questions).

Rejected: native function calling. It would need LAMB to forward `tools`.

### T5 — Three replaceable interfaces

`Retriever`, the tool protocol (`TextToolProtocol`) and the trace store
(`LocalTrace`), in `app/workshop/ports.py`. The assembler, chat flow and
panel depend only on these, so an implementation can change without
touching them: unit tests use a fake `Retriever`, the tool uses
`KbServerRetriever`.

### T6 — The frontend is a SvelteKit SPA served by FastAPI

SvelteKit with `adapter-static` (`fallback: 'index.html'`,
`paths.base = '/workshop'`, no SSR), Tailwind 4 and svelte-i18n. FastAPI
serves `frontend/build` at `/workshop`, and the API at `/workshop/api/*` is
included before that mount. The SSE frame types are the old branch's:
`observability`, `tool_event`, `error`, plus OpenAI-style content chunks.
The old protocol's `tool_messages` frame is never sent, because history is
assembled on the server.

Why: the ported components depend on SvelteKit (`$app/*`), and LAMB's own
frontend is SvelteKit, which keeps merging back into LAMB possible.

### T7 — The template's security core is not modified

`app/lti/*` and `app/sessions.py` stay as the template ships them. The
template calls them the security boundary, and leaving them alone keeps
template updates and a later merge simple.

Consequence: the template reads the session cookie before the header (see
[Template](#template)). The SPA works around that instead of changing
`sessions.py`. Reading the header and query before the cookie is worth
proposing to the template upstream.

### T8 — Retrieval runs in the tool's own lamb-kb-server

Option **8C** of the plan: a lamb-kb-server instance runs next to the tool,
and only the tool manages it: create collections, ingest, query, delete. The
image is pinned by digest
(`ghcr.io/lamb-project/lamb-kb:v0.7@sha256:5773572f…`), which has the same
source tree as `lamb-kb-server-stable/` in LAMB `874f5cd`.

Why: the supervisor's answer of 2026-10-21. It is the same component LAMB
uses, so what students see is how LAMB's RAG actually works, with no change
to LAMB and no retrieval written from scratch.

- Embeddings: `bge-m3` through Ollama by default. `scripts/compare_embeddings.py`
  (5 Catalan and Spanish Wikipedia articles, 309 chunks, 10 questions)
  found the right excerpt in the top 3 for 10/10 questions with `bge-m3`,
  against 2/10 with `nomic-embed-text`.
- The embedding model is fixed when a collection is created. Changing it
  means re-creating the collections and ingesting again.
- Do not use the separate `Lamb-Project/lamb-kb-server` repository. It is
  older than LAMB's copy and has diverged from it.
- Rejected: **8A**, adding knowledge-base search to LAMB's public API, and
  **8B**, embeddings + BM25 + sqlite-vec written into the tool.

### T9 — The tool is the kb-server's only client

The kb-server has one shared bearer token, no per-user access control, and
serves uploaded originals under `/static` without authentication. So:

- it has no published port (in development only `127.0.0.1:9190`, for
  host-side tests and scripts);
- its token is a strong random value;
- a collection id is only ever read from the tool's database
  (`kb_collections`), never taken from a request;
- the `file_url` / `source` links in chunk metadata never reach a browser.

This keeps the principle of the old branch's S1 fix, where a student could
read other students' knowledge bases.

### T10 — ~~One course collection per activity~~ (superseded by T15, 2026-10-24)

~~Teachers upload course material into one collection per activity, which
step 3 searches; a student's own document gets a collection only if needed.~~
Only student collections remain. Migration 4 moved the old course
collections to `kb_trash`.

### T11 — Development environment

A local upstream LAMB 0.8 (`874f5cd`) with the author's own account,
creator API key and engine assistants; a local Ollama for chat and
embeddings; `qwen3.5:9b` as the chat model, which fits in 12 GB of VRAM at
a 32k context. No paid API. The pilot will use a real LAMB instance and a
service account instead (Phase 9, Q2, Q7).

Why: development does not wait on anyone, only test data is on the machine,
and the pilot re-creates its collections anyway, so a different embedding
model there costs nothing.

### T12 — Step 2 puts the whole document into the context

The student's document, as extracted text, goes whole into the system
message: it is not chunked or searched. If it does not fit, it is cut
(T14). Only the extracted text is stored, never the file. In the old branch
step 2 ingested the document into a knowledge base, and step 3 queried it.

Why: it matches the proposal's wording ("this document becomes content the
assistant can use"), and whole document vs a few excerpts is itself a
teaching point. The other half of this decision (step 3 searches a teacher's
course collection) is superseded by T15.

### T13 — The engine assistant is a deployment setting

`engine_model` and `engine_context_tokens` are set on `/admin`. Teachers do
not pick an assistant. `/v1/models` returns only `lamb_assistant.<id>`, with
no names, and lists every published assistant the key's owner can see,
including the debug twin. A teacher could pick the twin, which only echoes
the messages back. The twin's id appears only in test environment variables.

### T14 — The tool keeps the request inside a context budget

LAMB passes no `num_ctx` to Ollama. When the input is too long, `llama3.2`
silently drops its start (the student's instructions go first), and
`qwen3.5:9b` returns an error that LAMB wraps as an answer. Either way the
panel would be wrong. So the tool budgets each request itself:

- Budget = `engine_context_tokens` × 0.85 − 3072 (reserved for the answer
  and the thinking). With the default 32768 that is 24780 tokens.
- When over budget, in this order: replace the results of earlier tool
  rounds with a note, oldest first (T16); truncate the document, ending on
  whitespace where possible and adding a `[Document truncated: …]` note;
  drop the oldest exchanges whole. If it still does not fit, raise
  `ContextOverflow`: HTTP 413, nothing is sent or stored.
- Every cut is recorded in the trace and shown in the panel.
- Counts are **estimates**, because LAMB reports no usage for Ollama
  (`-1`): tiktoken `o200k_base` × 1.15, with digits counted one token each
  (Qwen gives every digit its own token, so tables of numbers cost about
  1.5× the o200k count), plus 5 tokens per message and 16 per request.
  Calibrated against `qwen3.5:9b` with `scripts/calibrate_tokens.py`.
- On the host, set `OLLAMA_CONTEXT_LENGTH=32768`. Ollama's default is 4096.

### T15 — Steps 2 and 3 use the same document, the student's own

The author's decision, 2026-10-24.

- The text extracted in step 2 (the same text that goes into the context)
  is also ingested with `simple_ingest` (1000 / 200) into a collection of
  that student's own. There is one collection per student, activity and
  document: `student-<sha1(guid, link, user)>-<random>`, owner
  `taller-ia:<guid>:<link>:<user_id>`. A new document gets a new collection.
- Step 3 searches only that collection. With no document, step 3 is empty
  and does not block the student. There is no teacher material, and the
  `/course-kb` page is gone.
- Retrieval asks for 10 candidates with threshold −1 and marks the top 3
  (`RETRIEVAL_K`) as selected. The panel shows them all and where the cut fell.
- The step 5 switch (`rag_mode`), once the document is connected in step 3:
  **on** sends only the excerpts, without the document; **off** sends the
  whole document and also withdraws `kb_search`, so the two runs differ in
  that one thing (the old branch's C10). Not connected: the whole document
  goes, and step 4's `kb_search` still works. A new document starts out not
  connected.
- Removing or replacing a document, a teacher's purge and retention all
  delete the collection through `kb_trash`. If the kb-server cannot be
  reached, the deletion is retried at startup and once a day, whether or not
  retention is on.
- The consent notice says the document text also goes to the kb-server;
  the consent version became `taller-ia-2026-10-24`.

Why: comparing the whole document with a few excerpts of the *same*
material is more direct than comparing two different materials. When a long
document is truncated, retrieval can still find the part that was cut.
Cost: the document is stored in one more place, which the consent notice and
retention cover; step 3 waits a few seconds for chunking and embedding;
teachers cannot prepare material for the class.

### T16 — Tool rounds are part of the history

The author's decision, 2026-10-24.

Later turns resend each earlier tool round as the model wrote the call and
as the result went back, followed by the model's last reply. These are
stored apart from what the student saw: `turns.tool_rounds`
(`[{name, call, result}]`) and `turns.last_reply` (migration 5).
`assistant_content` is still the visible text (the words before each call,
then the last reply), and the chat, dashboard and feedback read only that.

When the budget is tight, the oldest results are replaced first by
`<tool_result name="…">[Earlier result left out to make room.]</tool_result>`.
Results shorter than the note are left alone, and the current turn's own
rounds are never replaced. Turns stored before migration 5 have no rounds and
are sent as before.

Why: Phase 6 had kept only the visible text in history. That was never
recorded as a decision, it was the opposite of the old branch, and in the
panel it looked like a bug. Chat APIs and common frameworks keep calls and
results, and trim old results only when the context runs short. Without them
later turns do not know what was already computed, and the history shows
numbers given with no call behind them, which is a bad example for the
model. Cost: more tokens per turn, hence the elision order in T14.

---

## Carried over from the old branch

`D` IDs are the design gaps found when the old branch was reviewed. `C` IDs
are decisions from its implementation plan. Only the IDs the code still
cites are listed, with how each was done here, which is sometimes simpler
than in the old branch.

| ID | Old branch | Here |
|---|---|---|
| D1 | The panel updated only after the answer | The `observability` "request" frame is sent before LAMB is called. `POST /workshop/api/preview` shows what the next message would send in steps 1–4, without calling LAMB. |
| D2 | The panel appeared only in step 5 | Per-step snapshots (`frontend/src/lib/components/logic/workshopObservations.svelte.js`): the document in step 2, the probe in step 3, the tools in step 4, the request and result in step 5. |
| D3 | No way to compare with and without the knowledge base | The step 5 switch (T15) and `RagComparison`. **New conversation** (migration 3; only the current conversation is history) stops the "off" run from seeing the "on" answer; the comparison card warns when both runs are in the same conversation. |
| D4 | "Teacher first" was only a label | A per-activity `teacher_first` setting: students land on `/waiting` until someone who launched the activity as an instructor has submitted their own run (`store.teacher_has_tried`). The old per-owner rules (C13) were not ported. |
| D5 | Reflection was an empty text box | Three guided questions in step 6. The answers are stored as JSON in `reflections` and go into the feedback transcript. |
| D6 | Formative feedback showed scores | The scores sit behind one toggle in `FormativeFeedback`. The total is computed by the tool from the rubric weights, not taken from the model. Feedback is never written to the gradebook. |
| D7 | `sandbox_exec` was an empty shell | Not offered. If it ever is: Pyodide in a Web Worker, so the server runs no student code. |
| C6 | Wizard language chain | `?lang=` (added at launch from `launch_presentation_locale`) → `localStorage.lang` → browser language → `en`. A guess is applied without being saved. |
| C7 | Minimal student shell | Title, activity name and the language switch; no navigation. The LMS provides the way back. |
| C8, C20 | Versioned consent; ask again when it changes | `service.CONSENT_VERSION`. A new version asks every student again. There is no per-activity opt-out here. |
| C9 | Retention by data category, with reminders | Simpler: off by default. With `WORKSHOP_RETENTION_DAYS`, a student's workshop data (assistant, document, conversation, traces, reflection, feedback, consent, KB collection) is deleted after that many days without use. A teacher can delete all student data of an activity at any time. Grades and enrollments are not touched. |
| C17 | The consent page asked LAMB which model would answer | The tool cannot see how LAMB resolves the model, so the teacher writes the model note in setup (`consent_model_note`). |
| C19 | Language switch in the student shell | en / es / ca / eu, saved to `localStorage.lang`. |

---

## External facts

Verified on 2026-10-20…24 against LAMB `874f5cd` (0.8) and `v0.7`, the
kb-server image above, and Ollama 0.40.6. They are not documented contracts:
check them again when a version changes. Code comments cite these as
`plan §0.1`, `§1` or `§8`.

### LAMB

- `/v1/chat/completions` keeps only `model`, `messages` and `stream`, and
  only `role` + `content` in each message. `tools`, the `tool` role and
  `temperature` are dropped. The same in 0.7, 0.8 and 0.9.
- An empty engine passes messages through unchanged (T1). A template of
  `{user_input}` would rewrite the last message to `"\n\n" + text + "\n\n"`.
- **Ollama failures arrive as normal answers.** A 400 (such as context
  overflow), a timeout or an empty reply comes back as HTTP 200,
  `finish_reason: "stop"`, with content `[Ollama Error] …`. When streaming
  it is one delta with that text, then `[DONE]`. `lamb_client` turns it into
  `LambError`, and a failed turn never becomes history. The timeout is
  LAMB's `OLLAMA_REQUEST_TIMEOUT`, 120 s by default.
- Other errors: internal exceptions → 422 with a traceback; 401, 403, 429
  and 503 are passed through.
- `usage` is −1 for Ollama, so token counts are estimates (T14).
- **No quota on the facade.** `/v1/chat/completions` does not check quotas,
  and usage is not logged for Ollama. The tool limits each student itself:
  `WORKSHOP_MESSAGES_PER_MINUTE` (6) and `WORKSHOP_MESSAGES_PER_DAY` (200).
  Over the limit it returns 429 and does not call LAMB.
- The model can be swapped for the organisation's default (not in the
  allow-list, an OpenAI failure, an Ollama 404). The answer's `model` field
  shows what answered. It is stored in the trace and shown as "Answered by".
- LAMB logs the first 100 characters of every message at INFO, and the
  full body at DEBUG. The consent notice says so (Q6).
- The Ollama connector forwards only `content`, so a thinking model's
  thinking is dropped. Thinking cannot be turned off through LAMB (`think`
  is not forwarded, and `/no_think` in the system message has no effect).
  While the model thinks, the stream sends nothing, so the student sees a
  few seconds of blank.
- `/v1/models` returns ids only (`lamb_assistant.<id>`), with no names.
- A creator API key needs the organisation's `api_access`. On 0.8 the
  system organisation reads `API_ACCESS_ENABLED` from the container
  environment at every start, and 0.8's compose file does not pass it in,
  so it needs a compose override.
- ghcr has no 0.8 image (`latest` is `v0.7`), so 0.8 has to be built from
  source with `--build`.
- The `debug-bypass` connector (the engine's debug twin) answers with
  `Messages:\n<json>\n\nBody:\n<json>`. When streaming it re-splits the text
  on whitespace, so the contract test uses `stream: false`.

### Template

- The session token is read from the `tool_session` cookie first, then the
  `X-Session-Token` header, then `?session=`. Every launch resets the cookie.
  In one browser, a teacher launch followed by a student launch turns the
  teacher's tab into the student. Hence the SPA sends only the header,
  with `credentials: 'omit'` (the template's own pages do the same now),
  and the e2e tests use a separate browser context per role. Sessions last
  8 h and cannot be renewed.
- On first boot the template seeds its runtime settings from
  `LAMB_API_BASE`, `LAMB_API_KEY`, `LTI_CONSUMER_KEY` and `LTI_SECRET` in the
  environment.

### kb-server

- One shared bearer token, read from `LAMB_API_KEY` *in the kb container*
  (with a public default if unset). No per-user access control. `/static`
  serves uploaded originals without authentication (T9).
- Similarity = 1 − cosine distance, so it ranges from −1 to 1. Results below
  the query's `threshold` are dropped, so the tool queries with −1 to show
  every candidate. Scores are not comparable across embedding models
  (`nomic-embed-text` scored wrong passages 0.7–0.8).
- Ingestion is asynchronous: the tool polls the job status.
- Collection names are globally unique (ChromaDB). Names include a hash and
  a random part, and a 409 is resolved by looking the name up.
- A chunk's `filename` metadata is the stored UUID name. The original name
  is found through `source`, which equals the file registry's `file_path`.
- `simple_ingest` ignores the file extension: extracted text stored under
  the original file name still chunks.
- Deleting a collection removes its vectors, stored files and records.
- Building the kb-server from source pulls an unpinned FastAPI that breaks
  it at import. Use the pinned image. (The same lesson is why the tool has
  `constraints.txt`.)

### Ollama (development machine)

- The default context is 4096 tokens unless `OLLAMA_CONTEXT_LENGTH` is set.
- `qwen3.5:9b` thinks for 120–380 tokens before each answer: short
  questions take 3–6 s warm, and an input of about 19k tokens 16–19 s. On
  open questions it sometimes thinks for more than 120 s and LAMB times out.
  That is the occasional failure of `tests/e2e_full.py` at "assistant
  streamed a reply"; it is not fixed.
- Without a `tools` parameter, a call the model writes stays in `content`.

---

## Not doing

- **A PR into LAMB** (#515): it will not be merged. Folding the tool back
  into LAMB is for later, following `docs/EXTENDING.md`.
- **8A / 8B** retrieval: see T8.
- **Native function calling, temperature experiments**: they need LAMB to
  forward parameters, and the supervisor prefers not to change LAMB. 0.8's
  LLM-access activity hands out keys to a LiteLLM gateway that may forward
  `tools`, but that bypasses the LAMB assistant. This is not verified; ask
  the supervisor before going that way.
- **LTI 1.3**: the template is LTI 1.1 (LAMB 0.9 has 1.3).
- **A code sandbox**: see D7.

---

## Open questions

Phase 9 (deployment and pilot) waits on Q2, Q6, Q7, Q8 and Q10. Everything
else can go ahead.

- **Q2** Which LAMB instance and version for the pilot? Who enables
  `api_access` there, and can there be a service account? Is `PLUGIN_BYPASS`
  enabled there (without it the contract test cannot run on the target)?
  Who registers the LTI tool in Moodle?
- **Q4** Does the repository stay a personal fork or move to the
  Lamb-Project organisation?
- **Q6** Who is responsible for student data, how long is it kept, and who
  reviews the consent notice? Data goes to the tool's SQLite, the kb-server
  (document text, chunks, vectors), LAMB (its logs) and LAMB's model provider.
- **Q7** Which servers run the tool and its kb-server? Local or external
  embeddings? Which chat model does the pilot use, and with what context
  length (that sets the T14 budget)? HTTPS with a public domain is required:
  the cookie is `Secure`, and `PUBLIC_BASE_URL` must be the URL the LMS sees.
- **Q8** Timeline: which term, course and teacher, and how many students?
  A single Ollama serves few requests at once, so a class asking together
  will queue.
- **Q10** What does the evaluation need to collect (surveys, pre/post tests,
  usage logs, panel interactions)? It may change the data model.
- **Tool-call format**: keep `tag` as the default, or switch to `qwen` (T4)?
  Both reach 10/10; `qwen` is about four times faster on `qwen3.5:9b`.

---

## Goals

The migration plan's completion criteria. Tests cite them (`plan G4`).

| ID | Goal | Checked by |
|---|---|---|
| G1 | A local LAMB 0.8 and mock LMS that start reliably | the key lists the engine and its debug twin on `/v1/models` |
| G2 | The template's own flow works in this repository | `tests/test_lti_launch.py`, `tests/e2e_full.py` |
| G3 | "A blank engine is transparent" has automated evidence | `tests/contract/test_lamb_transparency.py` |
| G4 | The panel shows exactly what is sent to LAMB | `tests/unit/test_assembler.py`; end to end, `tests/e2e_workshop.py` with the debug twin |
| G5 | Steps 1, 2, 4 and 5 work end to end, with the panel showing the input before the model answers | `tests/e2e_workshop.py` |
| G6 | Feedback, teacher side, teacher first, consent and retention ported | unit tests; the manual checklists are not done yet |
| G7 | Step 3 searches the student's document in the own kb-server, with every candidate shown | `tests/e2e_document_kb.py`, `KB_CONTRACT=1` contract tests |

---

## Phase log

What was built in each phase of the migration plan (cited as
`plan Phase N`).

| Phase | Done | What |
|---|---|---|
| 0 | 2026-10-22 | Local LAMB 0.8 with the engine (`qwen3.5:9b`) and its debug twin; mock LMS; Ollama context set to 32768 |
| 1 | 2026-10-22 | Repository from template `4ad73dc`; the template's flow working end to end |
| 2 | 2026-10-22 | Transparency contract test (T1) |
| 3 | 2026-10-22 | Assembler, trace, token estimates, LAMB errors; migrations and migration 1 (workshop tables) |
| 4 | 2026-10-22 | Frontend ported to SvelteKit; 6-step wizard; panel rewritten |
| 5 | 2026-10-22 | Steps 1, 2 and 5 end to end |
| 6 | 2026-10-22 | Text tool loop and calculator (T4) |
| 7 | 2026-10-22 | Formative feedback, teacher setup and dashboard, teacher first, consent, retention, rate limits |
| 8 | 2026-10-22 | Own kb-server (T8, T9), migration 2 (`kb_collections`), embedding comparison, new conversation (migration 3) |
| — | 2026-10-24 | Development stack in Docker; T15 (migration 4, `kb_trash`); T16 (migration 5) |
| 9 | — | Deployment and pilot: waiting on the open questions |

Not done from Phase 7: the manual test checklists (G6) have not been
rewritten or run, and the old dashboard's aggregate statistics were not
ported.
