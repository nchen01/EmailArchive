# Spec 04 — Storage Schema & Migrations (foundation)

> Maps the authoritative `packages/schemas` objects to physical Postgres + pgvector tables.
> Build-ready: an agent should be able to write the migrations and the persistence layer from
> this doc alone. This is **foundational** — L0 §12 (persist) and all of L1 write through it, so
> it lands in **Sprint 0** before S1 persistence.

**Stack (pinned):** PostgreSQL 16 · `pgvector` ≥ 0.7 · SQLAlchemy 2.0 (Core/ORM) · Alembic
(migrations). Source of truth for *shapes* remains `packages/schemas/models.py`; this spec defines
how those shapes are *stored*, not what they are.

---

## 1. Scope & principles

- **One database, many mailboxes.** Shared-schema multi-tenancy: every domain row carries a
  `mailbox_id`. Cheap for a 0-to-1; isolate hard via Row-Level Security (§6).
- **Two id schemes, preserved (schema convention #1–2).** Primary keys and internal foreign keys are
  UUIDs. `message_id_header` (RFC) is the citation key and the dedupe key — stored as its own
  `UNIQUE (mailbox_id, message_id_header)` column, never used as a foreign key.
- **Raw stays out of the DB.** Raw MIME and attachment bytes live in object storage; the DB holds
  pointers (`raw_uri`) and hashes only (schema convention, spec 00 §9/§12).
- **Idempotent writes.** Upsert keyed on `(mailbox_id, message_id_header)` for messages and on `id`
  elsewhere; re-ingest/re-enrich is a no-op (schema convention #5).
- **Sensitivity & noise are query-time filters.** They are columns with indexes so retrieval and
  synthesis can exclude tagged/noise rows cheaply (default `exclude`).

## 2. Extensions & conventions

```sql
CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "vector";     -- pgvector
```
- All tables: `id uuid PRIMARY KEY DEFAULT gen_random_uuid()` (except pure join tables),
  `mailbox_id uuid NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE`,
  `created_at timestamptz NOT NULL DEFAULT now()`, `updated_at timestamptz NOT NULL DEFAULT now()`.
- Enums (`role`, `sensitivity`, `event_type`, `label_source`) are stored as `text` with a `CHECK`
  constraint mirroring the Python enum values — easier to evolve than PG enums under migrations.
- Embedding dimension `D` comes from the mailbox's configured embedding model (§3 `mailbox.embed_dim`);
  the `vector(D)` column is created in a migration parameterized by that value (single-model MVP: one D).

## 3. Tenant root & operational tables

```sql
CREATE TABLE mailbox (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider        text NOT NULL CHECK (provider IN ('gmail','msgraph')),
  owner_email     text NOT NULL,
  owner_person_id uuid,                          -- set after identity resolution (spec 01 §3)
  status          text NOT NULL DEFAULT 'active',-- active | paused | retiring
  embed_model     text NOT NULL,                 -- shared with L2 (schema convention)
  embed_dim       int  NOT NULL,
  display_threshold real NOT NULL DEFAULT 0.4,   -- hide inferred facts below this
  config          jsonb NOT NULL DEFAULT '{}',   -- legal_domains, hr_senders, personal_domains, PARAMS
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sync_state (                         -- incremental ingest (spec 00 §13)
  mailbox_id   uuid PRIMARY KEY REFERENCES mailbox(id) ON DELETE CASCADE,
  sync_token   text,                              -- Gmail historyId / Graph delta token
  last_run_at  timestamptz
);

CREATE TABLE audit_log (                          -- append-only; never UPDATE/DELETE (spec 00 §12/§16)
  id            bigserial PRIMARY KEY,
  mailbox_id    uuid NOT NULL,                    -- intentionally no FK: survives mailbox deletion
  actor         text NOT NULL,                    -- OAuth subject
  action        text NOT NULL,                    -- ingest_run | export | delete | ...
  scope         text,
  message_count int,
  started_at    timestamptz NOT NULL,
  finished_at   timestamptz,
  sync_token    text
);

CREATE TABLE project_label_override (             -- sticky user renames (spec 03 §12)
  mailbox_id        uuid NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
  cluster_signature text NOT NULL,                -- sorted hash of high-weight thread ids
  label             text NOT NULL,
  updated_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (mailbox_id, cluster_signature)
);

CREATE TABLE schema_meta (                         -- mirrors models.SCHEMA_VERSION
  k text PRIMARY KEY, v text NOT NULL
);
```

## 4. L0 tables — messages & threads (spec 00)

```sql
CREATE TABLE thread (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  mailbox_id             uuid NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
  provider_thread_ids    text[] NOT NULL DEFAULT '{}',
  root_message_id_header text,
  subject_norm           text NOT NULL DEFAULT '',
  participants           text[] NOT NULL DEFAULT '{}',   -- normalized emails; OWNER INCLUDED (conv #4)
  t_start                timestamptz NOT NULL,
  t_end                  timestamptz NOT NULL,
  lineage_conflict       boolean NOT NULL DEFAULT false,
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE message (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  mailbox_id         uuid NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
  message_id_header  text NOT NULL,                       -- RFC; CITATION + DEDUPE key
  provider_id        text NOT NULL,
  thread_id          uuid NOT NULL REFERENCES thread(id) ON DELETE CASCADE,
  sender_email       text NOT NULL,                       -- normalized; resolves to Person via identity
  to_emails          text[] NOT NULL DEFAULT '{}',
  cc_emails          text[] NOT NULL DEFAULT '{}',
  addresses          jsonb NOT NULL DEFAULT '{}',         -- full Address objects (raw + display names)
  ts                 timestamptz NOT NULL,                -- UTC
  subject            text NOT NULL DEFAULT '',
  clean_text         text NOT NULL DEFAULT '',
  clean_text_tsv     tsvector GENERATED ALWAYS AS (to_tsvector('english', clean_text)) STORED,
  link_domains       text[] NOT NULL DEFAULT '{}',
  sensitivity        text[] NOT NULL DEFAULT '{none}',    -- CHECK each ∈ sensitivity values
  noise              boolean NOT NULL DEFAULT false,
  raw_uri            text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (mailbox_id, message_id_header)                  -- dedupe / idempotent upsert
);

CREATE TABLE message_attachment (                          -- AttachmentRef; content not stored
  message_id uuid NOT NULL REFERENCES message(id) ON DELETE CASCADE,
  sha256     text NOT NULL,
  filename   text,
  mimetype   text NOT NULL,
  size_bytes bigint NOT NULL,
  PRIMARY KEY (message_id, sha256)
);
```

## 5. L1 tables — people, graph, projects, events (specs 01 & 03)

```sql
CREATE TABLE org (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  mailbox_id uuid NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
  name text NOT NULL,
  domains text[] NOT NULL DEFAULT '{}',
  internal boolean NOT NULL DEFAULT false
);

CREATE TABLE person (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  mailbox_id uuid NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
  canonical_email text NOT NULL,
  names text[] NOT NULL DEFAULT '{}',
  org_id uuid REFERENCES org(id) ON DELETE SET NULL,
  role text NOT NULL DEFAULT 'unknown',            -- CHECK ∈ role values
  role_confidence real NOT NULL DEFAULT 0.0 CHECK (role_confidence BETWEEN 0 AND 1),
  UNIQUE (mailbox_id, canonical_email)
);

CREATE TABLE identity (                            -- address -> person (spec 01 §3)
  mailbox_id text NOT NULL,
  email text NOT NULL,
  display_names text[] NOT NULL DEFAULT '{}',
  person_id uuid REFERENCES person(id) ON DELETE CASCADE,
  PRIMARY KEY (mailbox_id, email)
);

CREATE TABLE edge (                                -- owner<->contact; owner implicit (conv #4)
  mailbox_id uuid NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
  person_id uuid NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  message_count int NOT NULL CHECK (message_count >= 0),
  sent_to_count int NOT NULL CHECK (sent_to_count >= 0),
  received_count int NOT NULL CHECK (received_count >= 0),
  first_contact timestamptz NOT NULL,
  last_contact timestamptz NOT NULL,
  weight real NOT NULL CHECK (weight >= 0),
  PRIMARY KEY (mailbox_id, person_id)
);

CREATE TABLE project (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  mailbox_id uuid NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
  label text NOT NULL,
  label_source text NOT NULL,                      -- CHECK ∈ label_source values
  start timestamptz NOT NULL,
  "end" timestamptz NOT NULL,                      -- quoted: end is reserved
  confidence real NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  debug jsonb
);

CREATE TABLE project_member (                      -- denormalized member_ids live implicitly here
  project_id uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  person_id uuid NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  involvement real NOT NULL CHECK (involvement >= 0),
  message_count int NOT NULL CHECK (message_count >= 0),
  PRIMARY KEY (project_id, person_id)
);

CREATE TABLE thread_project_assignment (           -- soft / overlapping membership (spec 03 §10)
  thread_id uuid NOT NULL REFERENCES thread(id) ON DELETE CASCADE,
  project_id uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  weight real NOT NULL CHECK (weight > 0 AND weight <= 1),
  is_primary boolean NOT NULL,
  PRIMARY KEY (thread_id, project_id)
);

CREATE TABLE event (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  mailbox_id uuid NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
  actor_person_id uuid NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  type text NOT NULL,                              -- CHECK ∈ event_type values
  summary text NOT NULL,
  project_id uuid REFERENCES project(id) ON DELETE SET NULL,
  source_message_ids text[] NOT NULL,              -- message_id_header values (NOT uuids)
  confidence real NOT NULL DEFAULT 0.0 CHECK (confidence BETWEEN 0 AND 1),
  CONSTRAINT ck_event_has_source CHECK (cardinality(source_message_ids) >= 1) -- "no citation, no claim"; cardinality() returns 0 for empty array, array_length() returns NULL (D8)
);
```

## 6. Embeddings & retrieval support

Vectors live next to the relational data so the (externally owned) query router can do filtered ANN
+ BM25 hybrid in one store. One row per message (chunk at message granularity for citation).

```sql
CREATE TABLE message_embedding (
  message_id  uuid PRIMARY KEY REFERENCES message(id) ON DELETE CASCADE,
  mailbox_id  uuid NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
  embedding   vector(D) NOT NULL,                  -- D = mailbox.embed_dim, set in migration
  ts          timestamptz NOT NULL,                -- denormalized for time-filtered ANN
  project_ids uuid[] NOT NULL DEFAULT '{}',        -- denormalized filters for fast pre-filtering
  person_ids  uuid[] NOT NULL DEFAULT '{}',
  sensitivity text[] NOT NULL DEFAULT '{none}',
  noise       boolean NOT NULL DEFAULT false
);
```
> The retrieval *logic* (routing, rerank) is the existing query router's, not this spec's. Storage
> only guarantees: vectors are present, filterable metadata is denormalized alongside, and BM25 is
> available via `message.clean_text_tsv`.

## 7. Indexes (Row-Level Security follows)

```sql
-- L0
CREATE INDEX ix_message_thread   ON message (mailbox_id, thread_id);
CREATE INDEX ix_message_ts       ON message (mailbox_id, ts);
CREATE INDEX ix_message_sender   ON message (mailbox_id, sender_email);
CREATE INDEX ix_message_fts      ON message USING gin (clean_text_tsv);
CREATE INDEX ix_message_live     ON message (mailbox_id) WHERE noise = false;  -- common filter
CREATE INDEX ix_attach_sha       ON message_attachment (sha256);              -- shared-artifact signal
CREATE INDEX ix_thread_parts     ON thread USING gin (participants);
-- L1
CREATE INDEX ix_edge_weight      ON edge (mailbox_id, weight DESC);
CREATE INDEX ix_tpa_project      ON thread_project_assignment (project_id);
CREATE INDEX ix_event_project    ON event (mailbox_id, project_id);
CREATE INDEX ix_event_srcs       ON event USING gin (source_message_ids);
-- embeddings: HNSW for cosine ANN (build after bulk load for speed)
CREATE INDEX ix_emb_hnsw ON message_embedding USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ix_emb_live ON message_embedding (mailbox_id) WHERE noise = false;
```

### Row-Level Security (defense in depth)
```sql
ALTER TABLE message ENABLE ROW LEVEL SECURITY;
CREATE POLICY mbx_isolation ON message
  USING (mailbox_id = current_setting('app.current_mailbox')::uuid);
-- repeat per tenant table; set `SET app.current_mailbox = '<uuid>'` at the start of each request/job.
```
Highly selective `mailbox_id` filters can hurt HNSW recall (pgvector iterative-scan caveat); for a
single-mailbox query path this is usually fine, but benchmark filtered ANN before relying on it.

## 8. Model → table map

| Pydantic model | Table(s) | Notes |
|---|---|---|
| `Address` | embedded (`message.addresses` jsonb + `*_email` cols) | not its own table in MVP (open decision §11) |
| `AttachmentRef` | `message_attachment` | content not stored; sha256 is the key |
| `Message` | `message` (+ `message_embedding`) | dedupe/cite on `message_id_header` |
| `Thread` | `thread` | `participants` includes owner |
| `Org` / `Identity` / `Person` | `org` / `identity` / `person` | |
| `Edge` | `edge` | owner implicit |
| `Project` | `project` | `start`/`"end"` quoted |
| `ProjectMember` | `project_member` | supplies `Project.member_ids`/`members` on read |
| `ThreadProjectAssignment` | `thread_project_assignment` | soft membership |
| `Event` | `event` | `source_message_ids` = header values; non-empty enforced |
| `ClusteringResult` | (not persisted) | assemble from `project` + assignments at read time |

## 9. Migrations (Alembic)

- Forward-only, reviewed migrations. First migration creates extensions (§2) then tables.
- **pgvector / HNSW and `tsvector` generated columns are NOT reliably autogenerated** — hand-write
  those `op.execute(...)` statements; do not trust `--autogenerate` for them.
- The `vector(D)` dimension is templated from the single configured embedding model; changing models
  is a migration (new column + backfill), and bumps `models.SCHEMA_VERSION` + a `schema_meta` row.
- Seed `schema_meta('SCHEMA_VERSION', <models value>)` in the baseline migration; assert match on boot.

## 10. Retention & deletion (privacy, spec 00 §16)

- **Mailbox teardown / TTL:** `DELETE FROM mailbox WHERE id = ?` cascades to all domain rows;
  `audit_log` is intentionally retained (no FK).
- **Data-subject deletion (a third party requests erasure):** delete their `person` + `identity`
  rows, scrub their address from `message.sender_email`/`to_emails`/`cc_emails`/`thread.participants`,
  and null any `clean_text` spans authored by them. This is genuinely hard because their words live
  inside threads with others — treat the exact policy as an open decision (§11) and keep an audit row.
- Object-store raw MIME is deleted on the same triggers (separate lifecycle job).

## 11. Acceptance / Definition of Done

- [ ] Alembic migrations apply cleanly from an empty DB and are forward-only.
- [ ] Round-trip: an `ekc_schemas` object persisted and re-read equals the original.
- [ ] `(mailbox_id, message_id_header)` upsert is idempotent (re-ingest = no new rows).
- [ ] `event` insert with empty `source_message_ids` is rejected by the DB CHECK.
- [ ] pgvector HNSW cosine query returns nearest neighbors; BM25 via `clean_text_tsv` works.
- [ ] RLS isolates two mailboxes in the same DB (cross-tenant read returns nothing).
- [ ] `schema_meta.SCHEMA_VERSION` matches `models.SCHEMA_VERSION` on boot (assert).

## 12. Sprint task breakdown (`@sprint S0`, foundation — precedes S1 persistence)

| # | Ticket | Done when |
|---|---|---|
| 4.1 | Baseline migration: extensions + `mailbox`, `sync_state`, `audit_log`, `schema_meta` | empty DB → tenant root exists |
| 4.2 | L0 migration: `thread`, `message`, `message_attachment` + indexes + FTS column | L0 §12 can persist |
| 4.3 | L1 migration: `org`, `person`, `identity`, `edge` + indexes | identity resolution + graph can persist |
| 4.4 | Projects migration: `project`, `project_member`, `thread_project_assignment`, `event` + CHECKs | clustering + events can persist |
| 4.5 | `message_embedding` + HNSW index (templated `vector(D)`) | filtered ANN query works |
| 4.6 | RLS policies + `app.current_mailbox` session wiring | two-tenant isolation test passes |
| 4.7 | SQLAlchemy models + Pydantic↔row mappers + round-trip tests | DoD round-trip green |
| 4.8 | `project_label_override` + sticky-rename read path | rename survives re-cluster |

## 13. Open decisions

- **Tenancy model:** shared-schema + RLS (this spec) vs schema-per-tenant vs DB-per-tenant. Revisit
  at scale / for enterprise isolation requirements.
- **Normalize `Address`?** A dedicated `address` table would ease data-subject deletion and dedupe
  display names, at the cost of more joins. Deferred; MVP embeds it.
- **Data-subject deletion semantics** for third-party content inside shared threads (scrub vs
  tombstone vs redact-in-place) — needs a product + legal decision.
- **Embedding dimension** is single-valued in MVP; multi-model support means a per-model column or a
  separate index per model.
- **Where the existing query router expects vectors** — confirm it reads `message_embedding` (or
  adapt). `// TODO: align with the router's storage assumptions.`
