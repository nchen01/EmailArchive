# S4 Implementation Guide — Event Extraction + L3 Grounded Synthesis

This is an **implementation guide**, not a spec. It bridges the existing specs (01 §7, 02 §6,
05 §5, implementation-plan §4) and the build. Read it before writing code; use it to catch spec
drift before implementation. Precedence when docs disagree:
`ekc_schemas/models.py` → layer spec → implementation-plan → README → AGENTS (AGENTS §9).

S4 introduces the **first and only LLM call in the system** (AGENTS §3 #9: "the LLM appears only
in L3"). Everything upstream is deterministic. Two new behaviors land: deterministic Event
extraction (which feeds the existing `activity` field) and nondeterministic L3 synthesis (isolated
behind the citation contract). They are built on separate tracks and converge only at the surfaces.

---

## 1. S4 scope and non-scope

### In scope
- **Event extraction** (`services/enrich/events.py`) — spec 01 §7. Per-thread, structured-output
  LLM call producing `ekc_schemas.Event` rows. Wired into `run_enrichment`. Populates the `event`
  table (migration 0004, currently always empty).
- **L3 synthesis engine** (`services/synthesis/`, new service) — implementation-plan §4. Two query
  surfaces:
  - **"What's been done"** on a project (spec 02 §6) — project view `activity` panel.
  - **"Ask about this contact"** (spec 05 §3.4) — the disabled network-map button gets enabled.
- **API**: populate `ProjectDetailOut.activity` from the `event` table; two new POST synthesis
  endpoints.
- **Frontend**: render Events in the "What's been done" panel; enable both L3 buttons with loading
  and error states.

### Explicitly NOT in scope (deferred — implementation-plan §8)
- Multi-mailbox, the **offboarding motion**, cross-channel (Slack/docs) ingestion.
- The free-text **"Cover-for-me" query** entry point (implementation-plan §6.3). S4 ships the two
  *scoped* L3 surfaces above (project summary, contact summary), NOT the open natural-language box.
- **L2 retrieval layer** — externally owned (AGENTS §6). S4 synthesis assembles its own context
  from L1 objects directly; it does NOT call a vector store. (`message_embedding` table still
  deferred.)
- Any learned/classifier upgrade to event extraction — v1 is a single structured LLM call.
- Background/batch synthesis — synthesis is strictly user-triggered (see §5, §9).

---

## 2. Prerequisites (what must already be done)

S4 consumes L1 output materialized by S1–S3. All present:

| Consumed object | Produced by | Status |
|---|---|---|
| `Person`, `Identity`, `Org` | S1 identity resolution | ✓ |
| `Edge` | S1 graph | ✓ |
| `Project`, `ProjectMember`, `ThreadProjectAssignment` | S3 clustering | ✓ |
| `Thread`, `Message` (incl. `sensitivity`, `clean_text`, `message_id_header`) | L0 | ✓ |
| `event` table + `cardinality()` CHECK + GIN index | migration 0004 (+0005 fix) | ✓ |

### Known gaps S4 must close
- `services/api/routers/project_view.py:228` returns `activity=[]` with a `# S4` marker. S4
  populates it from the `event` table.
- `ProjectDetailOut.activity: list[dict]` (`services/api/schemas/project_view.py:70`) is a loose
  `dict`. S4 should replace it with a typed `ActivityItemOut` DTO (see §7) derived from `Event`.
- `services/synthesis/` does not exist.
- The network-map "Ask about this contact" button is disabled with an S4 tooltip (spec 05 §3.4,
  §8). S4 enables it.
- **No Anthropic SDK / API key wiring exists yet.** This is net-new infra; the key must be read
  through an interface analogous to `get_token()` (D6), never hardcoded, never logged.

---

## 3. Build order with hard dependency constraints

S4 has two **independently testable** tracks that converge at the surfaces. Build both to a
fixture-validated state before wiring. Ticket numbering mirrors spec 03's style.

### Track A — Event extraction (deterministic at the seam, LLM behind an injected fn)
- **4.1** `services/enrich/events.py`: `extract_events(threads, messages, assignments, *, extract_fn, params) -> list[Event]`.
  Pure orchestration; the LLM call is injected as `extract_fn` (mirrors `embed_fn`/`nlp` in
  clustering, pipeline.py:42). Owner filtering, sensitivity gate, project_id attachment, dedup key.
- **4.2** Default production `extract_fn` (Anthropic structured output) in
  `services/enrich/events_llm.py`. Isolated so 4.1 tests never touch the network.
- **4.3** Wire `extract_events` into `run_enrichment` (pipeline.py), gated like clustering: runs
  only when `threads` is supplied AND an `extract_fn` is available; returns `[]` otherwise.
- **4.4** Persist Events (idempotent upsert, §4 dedup) + populate `ProjectDetailOut.activity`.
- **4.5** Track-A eval (`services/enrich/events/eval/run_eval.py`, modeled on
  `clustering/eval/run_eval.py`). Structural gates only (§10) — NOT byte-identical.

### Track B — L3 synthesis engine (nondeterministic, isolated)
- **4.6** `services/synthesis/` skeleton: `client.py` (Anthropic wrapper + caching + key check),
  `params.py` (model id, max_tokens, etc.), `contracts.py` (request/response Pydantic models with
  the citation validator).
- **4.7** `synthesize_project(project, events, threads, *, synth_fn) -> SynthesisResult` — "What's
  been done" (spec 02 §6).
- **4.8** `synthesize_contact(person, edge, threads, *, synth_fn) -> SynthesisResult` — "Ask about
  this contact" (spec 05 §3.4).
- **4.9** Two POST API endpoints (§7) + 503-on-missing-key.
- **4.10** Track-B tests with a mocked `synth_fn`: citation-validator rejection, uncited-claim
  rejection, empty-events graceful path, key-absent 503.

### Convergence
- Frontend (**4.11**) wires the project-view panel and network-map button to the §7 endpoints.
- **Hard constraint:** Track A (4.1–4.5) and Track B (4.6–4.10) are independently testable with no
  LLM network call (both inject a deterministic fn). Do not wire the real Anthropic client into
  `run_enrichment` or the API until both tracks pass their fixture tests. The convergence point is
  the API layer: `activity` is read from the `event` table (Track A output) and the synthesis
  endpoints read Events + Threads (Track A output) to build context for Track B.

---

## 4. Event extraction design (Track A)

Spec 01 §7 is intentionally brief; this section is the build-precise version.

### Module & signature
`services/enrich/events.py`. The LLM is injected, exactly like clustering injects `embed_fn`:

```text
extract_events(threads, messages_by_thread, assignments, *, extract_fn, params) -> list[Event]
```
`extract_fn(thread_context) -> list[ExtractedEventRaw]` is the only nondeterministic dependency.
Tests pass a deterministic fake; production passes the Anthropic-backed fn (4.2). This is the
contract boundary that keeps 4.1 testable offline.

### Model
Claude via the Anthropic API, with **structured output** (Pydantic tool-use / `instructor`-style),
NOT freeform text parsed by regex. **Same model and cost bucket as L3 synthesis** (§5) — one model,
one config key. Default `claude-sonnet-4-6` (see §11 open decision); stored in config, never
hardcoded.

### Input granularity
**Per-thread** (spec 01 §7: "Run per thread with the schema enforced"). One LLM call per
non-excluded thread. Context = the thread's messages (`clean_text`, sender, ts,
`message_id_header`). Do not batch multiple threads into one call — per-thread keeps citations
unambiguous and bounds context size.

### Owner filtering (precision risk — flag)
The owner is a participant of (almost) every thread and carries no discriminative signal
(`ekc_schemas` convention #4; AGENTS §3 #5). **Do not extract Events where the owner is the sole
actor unless their action is independently evidenced in the message text.** When the owner is the
only identifiable actor and the evidence is weak, lower `confidence` rather than fabricate an Event.
This is the **owner-as-actor inflation** risk (§9).

### Sensitivity gate
Threads with any `Message.sensitivity != [NONE]` are **excluded by default**
(`sensitivity_mode = exclude`, the same gate clustering uses; AGENTS §3 #9). A thread is excluded
if any of its messages is sensitivity-tagged. Honor exactly once, in `events.py`, before calling
`extract_fn`. Excluded threads produce zero Events (DoD §10).

### Citation enforcement (grounding contract, D8 extended)
Every Event MUST carry ≥1 `source_message_ids`, holding **`message_id_header` values, not UUIDs**
(AGENTS §3 #2, #3; `ekc_schemas` convention #1, #2). The `Event` schema enforces
`min_length=1`; the DB enforces `cardinality(...) >= 1` (migration 0004, D8). The **prompt must
also reinforce it** ("every event must reference the message_id(s) it is drawn from") so the model
returns citations rather than getting rejected at construction. Map the model's returned message
references to `message_id_header` before constructing the `Event` — if the model returns an
internal index, resolve it to the header here.

### project_id attachment
After extraction, attach `project_id` by looking up the thread's **primary**
`ThreadProjectAssignment` (`is_primary == True`). A thread may belong to multiple projects (soft
assignment, spec 03 §10); attach the primary project_id. Threads with no assignment get
`project_id = None` (valid per schema) — the Event still persists but won't appear in any project's
`activity`.

### Epistemic grain (decision table for the prompt)
Three `EventType`s. **Never upgrade intent to outcome** (spec 01 §7; AGENTS §3 #8). Give the model
this table with concrete fixture examples:

| Text signal | Type | Example |
|---|---|---|
| Future tense / intent / commitment | `proposed` | "I'll re-shard the cluster tonight" · "We should send the contract" |
| Past-tense action, no confirmed result | `did` | "Pushed the migration branch" · "Sent the SOW to procurement" |
| Confirmed result, evidenced in the text | `outcome` | "Staging cutover completed and verified" · "Contract signed, countersigned copy attached" |

Hard rules for the prompt: do **not** infer `outcome` from message volume or tone; if no outcome is
stated, **do not emit one**; `summary` is one factual clause, no adjectives (`ekc_schemas`
`Event.summary`).

### Determinism strategy
Event extraction involves an LLM and is **inherently nondeterministic**. Strategy:
- **Tests:** inject a deterministic `extract_fn` (same pattern as `embed_fn`). No network in tests.
- **Production:** accept nondeterminism. **Do NOT add a byte-identical determinism DoD gate** to
  event extraction (this is the one place AGENTS §3 #6 / §8 byte-identical determinism does not
  apply — flag this explicitly to the reviewer so the eval isn't built wrong). The eval gate is
  **structural** (§10): every Event parses and has ≥1 citation.

### Deduplication (re-run safety)
Re-running enrichment must not double Events. The `event` table has no natural unique constraint
today. Use an **idempotent upsert keyed on a content hash** consistent with AGENTS §3 #6 /
`ekc_schemas` convention #5 (enrichment is a pure function of inputs + content hash). Proposed
natural key: `(mailbox_id, actor_person_id, type, source_message_ids[0], summary_hash)` where
`summary_hash = sha256(summary)[:16]`. **Confirm the dedup key with the reviewer** (§11) — because
the LLM may produce slightly different `summary` text across runs, a pure-content key can still
double rows. Mitigation options to weigh: (a) delete-and-reinsert per (mailbox_id, thread set) on
re-run, or (b) upsert on the key above accepting occasional near-dupes. Recommend (a) for
correctness, scoped to the threads re-processed.

---

## 5. L3 synthesis engine design (Track B)

The only nondeterministic component, isolated behind the citation contract.

### Service location
`services/synthesis/` — a **new service, separate from `enrich`**. It does not run inside
`run_enrichment`; it is invoked synchronously by the API on explicit user action.

### Grounding contract
Every claim in a synthesis response must cite ≥1 `message_id_header`. **"No citation, no claim"**
(implementation-plan §4; AGENTS §3 #3; D8 spirit). Enforcement mechanism: the Anthropic call uses
**structured output** so each claim is a `{text, source_message_ids[]}` object, and a **Pydantic
validator rejects any claim with an empty `source_message_ids` before the response leaves the
synthesis layer.** Uncited claims never reach the API. The validator is the Track-B analogue of the
`Event` schema's `min_length=1`.

### Two query surfaces (S4)
**(a) "What's been done" on a project** (spec 02 §6)
- Input context: the project's `Event` rows (Track A output) + recent `Thread`s (subjects,
  participants, timestamps).
- Output: a grounded summary, per-claim citations, **epistemic labels carried through**
  (`proposed`/`did`/`outcome`), **no invented outcomes**. Render `outcome > did > proposed`
  (spec 02 §6).

**(b) "Ask about this contact"** (spec 05 §3.4 — the disabled button)
- Input context: the contact `Person` + their `Edge` stats + shared `Thread`s (subjects, ts).
  Whether Events from the contact's threads are in scope is an **open decision** (§11) — default to
  including them, since they are already grounded.
- Output: a grounded relationship summary (what this contact works on with the owner, in what
  capacity), every claim cited to a thread/message. No invented claims.

### Model
Claude via the Anthropic API. Default **`claude-sonnet-4-6`** (current environment default; §11).
**Configurable, stored in mailbox config, never hardcoded** — changing the model is a config change,
not a code change (§9 model-version pinning). Same model id as event extraction (one cost bucket).

### Prompt caching
The same project/contact context may be queried repeatedly. Use the Anthropic API's
`cache_control` on the **context portion** of the prompt (the large, stable block: system prompt +
Events + thread digest), so repeat queries hit the cache and cost less. **The `claude-api` skill in
this environment handles prompt caching — use it** when implementing `services/synthesis/client.py`.
Verify cache hits via the response `usage` metadata (`cache_read_input_tokens`) — this is a DoD item
(§10).

### Anti-patterns the system prompt MUST enforce
- **Volume ≠ accomplishment** (AGENTS §3 #8; D8 spirit) — never render "sent 200 emails" as
  "drove to completion."
- **Proposed ≠ outcome** — carry the epistemic label; never upgrade.
- **Partial-record framing** — "email is one channel; real work also lives in Slack/docs/meetings;
  surface what is evidenced, flag what cannot be seen" (implementation-plan §4).
- **No confidence language when evidence is indirect** — prefer "coordinated across N threads; final
  outcome not visible in email" over a confident fabrication.

### Rate limiting / cost
L3 is the expensive layer. **Synthesis calls are gated behind explicit user actions** (clicking
"Generate summary" or "Ask about this contact"). **Never background-batch synthesis** for all
projects/contacts (§9 cost control). One user action → one synthesis call.

### Error handling
If the Anthropic call fails, the key is missing, or the validator rejects uncited claims, return a
**structured error** the API layer surfaces gracefully (§7). **Never surface a raw LLM error or
traceback to the user.** Key-absent is a 503 with a clear message (§7).

---

## 6. Database — no new migration (decision: no synthesis_log in S4)

- The `event` table already exists (migration 0004): `id`, `mailbox_id`, `actor_person_id`,
  `type` CHECK, `summary`, `project_id` (FK `ON DELETE SET NULL`), `source_message_ids text[] NOT
  NULL`, `confidence`, the `ck_event_has_source` CHECK using `cardinality()` (D8), `ix_event_project`
  and `ix_event_srcs` (GIN) indexes. **No new table for Events** — S4 only populates rows.
- `ProjectDetailOut.activity` is already a field (default `[]`); S4 fills it from the `event` table.

### Synthesis cache/log table — decision: **NO for S4** (revisit in §11)
S4 ships without a `synthesis_log` table. Synthesis is user-triggered and not cached at the DB
level (prompt caching at the API covers repeat-query cost). **Rationale:** adding a table is reversible
later; shipping the two surfaces is the S4 goal. **However**, §11 argues for a `synthesis_log`
(request hash, response, citations, ts) for audit/replay and per-mailbox budget tracking — the
reviewer decides. If yes, it becomes **migration 0006** and Track B writes to it after each call.
(Note: migration 0006 is currently reserved in AGENTS §6 for the deferred `message_embedding`
table — coordinate the number if both land.)

---

## 7. API changes

### Existing — now populated
`GET /api/projects/{mailbox_id}/{project_id}` (`project_view.py`) — S4 populates `activity` from
the `event` table: select `event` where `project_id == this`, ordered by epistemic grade
(`outcome > did > proposed`) then ts. **Replace `activity: list[dict]`** with a typed
`ActivityItemOut { type, summary, actor (person_id), source_message_ids, confidence }` derived from
`Event` (spec 02 §5 shape). Hard invariant (spec 02 §5): every `activity` item ships ≥1
`source_message_ids`; the API rejects any Event without one (the DB CHECK already guarantees this,
but assert it at the DTO boundary too).

### New — synthesis endpoints
- `POST /api/synthesis/{mailbox_id}/project/{project_id}` — triggers L3 "What's been done".
- `POST /api/synthesis/{mailbox_id}/contact/{person_id}` — triggers L3 "Ask about this contact".

Both:
- **POST** (not GET) — they trigger an LLM call and are not cacheable like a read (cost-bearing,
  side-effecting if a `synthesis_log` is later added).
- Return a structured cited response: `{ claims: [{text, source_message_ids[]}], state?, model, usage }`.
- **503 with a clear message if the Anthropic API key is not configured** (not a 500 traceback).
- 404 if the mailbox/project/person does not exist (reuse the existing `_get_mailbox` pattern).
- Empty-Events project → return an empty/`"no evidenced activity in email"` response, **not** an
  error (DoD §10).

---

## 8. Frontend changes

### Project view "What's been done"
Currently renders nothing (S3 placeholder; review-notes line 98–107). Two steps:
1. **Render Event rows** from the existing `activity` field (now populated), grouped by epistemic
   grade `outcome > did > proposed`, each line showing its citation chip(s) (spec 02 §6). A
   `proposed` event must never render under the "confirmed/outcome" heading (spec 02 §6 acceptance).
2. Add a **"Generate summary"** button that calls
   `POST /api/synthesis/{mailbox_id}/project/{project_id}` and renders the cited synthesis response.

### Network map contact panel
The "Ask about this contact" button is disabled with an S4 tooltip (spec 05 §3.4, §8). **Enable it**
to call `POST /api/synthesis/{mailbox_id}/contact/{person_id}` and render the cited result in the
right drawer.

### Both interactions
- Show a **loading state** during the call (L3 is slow).
- **Handle errors gracefully** — 503 (key absent) → "Summaries are not configured"; other errors →
  generic retry message. Never show a raw error body.
- Render citation chips as provenance deep-links (same format TBD as spec 05 §9 thread provenance).

---

## 9. Things to beware of (reviewer checklist)

- **Citation enforcement.** The LLM may return Events or synthesis claims without citations. Events
  are rejected at construction (`min_length=1`) and at write time (DB CHECK, D8). **Synthesis needs
  an equivalent Pydantic validator before the response leaves the synthesis layer.** Never let an
  uncited claim reach the API response.
- **Volume ≠ impact trap.** The extraction prompt must not treat message volume as evidence of
  accomplishment (AGENTS §3 #8). The synthesis system prompt must say this explicitly. **Review both
  prompts for this before merge.**
- **Epistemic upgrades.** "I'll send the contract" must never become an `outcome`. Test with a
  future-tense fixture message.
- **Owner-as-actor inflation.** The owner appears in every thread. Events where the owner is the
  only identifiable actor get **lower confidence** unless their action is directly evidenced in text.
- **LLM determinism vs eval.** Event extraction is nondeterministic; **do NOT add a byte-identical
  determinism gate** (the one exception to AGENTS §3 #6). Do add a **structural** gate: every
  extracted Event parses without error and has ≥1 `source_message_id`.
- **Sensitivity exclusion.** L0-tagged sensitive threads (privileged/legal/HR/personal) are excluded
  from extraction and synthesis by default — same `sensitivity_mode=exclude` gate as clustering
  (AGENTS §3 #9).
- **Cost control.** L3 calls are expensive. Gate behind explicit user actions; never batch. Add a
  per-mailbox request log only if budget tracking is needed (ties to the §11 `synthesis_log`
  decision).
- **Prompt caching.** Use `cache_control` on the context portion. The `claude-api` skill handles
  this — use it. Verify via `usage` metadata.
- **Model version pinning.** The Claude model id is **config, not code**. Changing it is a config
  change. Both extraction and synthesis read the same id.
- **ID scheme.** Citations are `message_id_header`, never UUIDs (AGENTS §3 #2). Easy to get wrong
  when mapping LLM output back to Events — assert it.
- **Key handling.** The Anthropic key is read through an interface (D6 pattern), never in the DB or
  logs.

---

## 10. Eval / Definition of Done

- [ ] Every `event` row has ≥1 `source_message_id` (DB CHECK enforces; **test it** — attempt an
      empty insert, assert rejection).
- [ ] "I'll send the contract" → extracted as `proposed`, never `outcome`. Fixture test.
- [ ] A thread with discussion but no stated result → `proposed`/`did` events only, **no
      `outcome`**. Fixture test.
- [ ] Sensitivity-tagged threads produce **0 events** (exclusion enforced). Fixture test.
- [ ] Synthesis response for a project cites ≥1 `message_id_header` per claim/activity item.
- [ ] Synthesis handles a project with **0 events** gracefully (empty/"no evidenced activity"
      response, not an error).
- [ ] "Ask about contact" response cites threads, not invented claims.
- [ ] **Anthropic API key absent → 503**, not a 500 traceback. Test.
- [ ] Project view `activity` populated from the DB after event extraction runs (integration test).
- [ ] **Prompt caching implemented**; verified via Anthropic `usage` metadata
      (`cache_read_input_tokens > 0` on a repeat query).
- [ ] Track-A eval (`services/enrich/events/eval/run_eval.py`) runs against the fixture: structural
      gates pass (every Event parses + cited); **no byte-identical gate**.

---

## 11. Open decisions (confirm before build starts)

1. **Claude model for extraction + synthesis.** Suggest `claude-sonnet-4-6` (current environment
   default). Must be the **same** id for both (one cost bucket). Confirm and record in config + a
   decision-log entry (D10).
2. **Event deduplication on re-run.** Delete-and-reinsert (scoped to re-processed threads) vs upsert
   on a content key. Recommend delete-and-reinsert for correctness, because the LLM may vary
   `summary` text across runs and defeat a pure-content upsert key. Reviewer picks; record the
   natural key if upsert.
3. **`synthesis_log` table (audit/replay/budget).** Argue **for** a `synthesis_log` (request hash,
   response, citations, ts, model, token usage): it enables replay without re-spending, per-mailbox
   cost tracking, and an audit trail consistent with the system's provenance posture. S4 ships
   without it by default; reviewer decides yes/no. If yes → migration 0006 (coordinate the number
   with the deferred `message_embedding` table, AGENTS §6).
4. **"Ask about contact" evidence scope.** Does synthesis include `Event`s from the contact's
   threads, or only `Edge` stats + `Thread` subjects? Default: include Events (already grounded).
   Confirm — it changes the context size and the citation density.
5. **Per-user rate limiting / cost guardrails at S4.** Is per-user/per-mailbox rate limiting needed
   now, or deferred? S4 gates synthesis behind explicit user actions (one click = one call), which
   is a soft guardrail. Decide whether an explicit rate limit / budget cap is required before ship.
