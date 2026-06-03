# Spec 00 — L0 Ingest & Normalization (deep dive)

> The foundation. Turns a raw mailbox into the canonical `Message` + `Thread` contract that
> every later layer assumes. Build-ready: an engineer or agent should implement it from this
> doc alone. Everything downstream (identity resolution, relationship graph, clustering,
> retrieval, synthesis) consumes only what this spec defines.

**Service:** `services/ingest` · **Layer:** L0 · **Depends on:** mailbox OAuth grant + provider
API · **Feeds:** L1 (spec 01 §3 identity resolution is the first consumer).

**Stack (pinned):** Python 3.11 · `google-api-python-client` (Gmail) / `msgraph-sdk` (M365) ·
`authlib` (OAuth) · stdlib `email` + `mail-parser` (MIME) · `talon` (quote/signature stripping) ·
`beautifulsoup4` + `html2text` (HTML→text) · `charset-normalizer` (encoding) · `tldextract`
(Public Suffix List) · Redis-backed task queue for fetch/rate-limit handling.

---

## 1. Scope & goals

Input: one mailbox, reachable via an admin OAuth grant. Output: normalized, deduped,
noise-flagged, sensitivity-tagged `Message` objects grouped into reconstructed `Thread`s.

L0 does **not** assign people (`person_id`) — it emits normalized email addresses; identity
resolution (spec 01 §3) is the bridge to people. This boundary is deliberate and load-bearing.

Goals, in priority order:
1. **Fidelity of provenance** — every `Message` keeps its RFC 5322 `Message-ID`; this is the
   citation key the whole product grounds on. Never lose or mutate it.
2. **Clean text** — `clean_text` (quoted replies + signatures stripped, charset-normalized) is
   what gets embedded later; its quality gates clustering and retrieval quality.
3. **Correct thread lineage** — reconstruct from headers, not subject lines.
4. **Safety at the boundary** — noise and sensitive content are flagged *here* so no later layer
   has to re-derive them. This is the layer where privacy controls live.
5. **Idempotent + incremental** — re-ingest or partial sync must not duplicate or churn.

## 2. Definitions & invariants

- An `Address` is a normalized email + observed display names. No person resolution at L0.
- A `Message` is one normalized email with provenance, clean text, tags.
- A `Thread` is a set of messages sharing a reconstructed conversation lineage.
- **Invariants (enforced at write time):**
  - Every `Message` has a non-empty `message_id_header` (synthesized if the source lacked one — §6).
  - Dedup key is `message_id_header`; the same email seen in two folders yields one `Message`.
  - Every `Message` belongs to exactly one internal `Thread`.
  - `clean_text` is valid UTF-8; raw MIME is archived separately (object store), never inline.
  - Re-ingesting an unchanged mailbox is a no-op (idempotent upsert by content hash).

## 3. Data contracts (`packages/schemas`)

> **Authoritative definitions live in `packages/schemas/models.py`.** The code below is
> illustrative context for this spec — import the real models, do not re-declare them.

```python
from pydantic import BaseModel
from datetime import datetime
from enum import Enum

class Address(BaseModel):
    raw: str                      # original header token, kept for audit
    email: str                    # normalized: lowercased, plus-addressing stripped
    display_names: list[str] = []

class AttachmentRef(BaseModel):
    sha256: str                   # content hash — the only thing retained by default
    filename: str | None
    mimetype: str
    size_bytes: int

class Sensitivity(str, Enum):
    NONE = "none"; PRIVILEGED = "privileged"; LEGAL = "legal"
    HR = "hr"; PERSONAL = "personal"

class Message(BaseModel):
    id: str                       # internal UUIDv4
    message_id_header: str        # RFC 5322 Message-ID — PROVENANCE / CITATION KEY
    provider_id: str              # Gmail/Graph native id
    thread_id: str                # internal reconstructed thread id
    sender: Address
    to: list[Address] = []
    cc: list[Address] = []
    ts: datetime                  # UTC
    subject: str
    clean_text: str               # normalized body for embedding/search
    attachment_refs: list[AttachmentRef] = []
    link_domains: list[str] = []  # registrable domains (PSL)
    sensitivity: list[Sensitivity] = [Sensitivity.NONE]
    noise: bool = False
    raw_uri: str | None = None    # object-store pointer to archived raw MIME

class Thread(BaseModel):
    id: str                       # internal UUIDv4
    provider_thread_ids: list[str] = []
    root_message_id_header: str | None = None
    message_ids: list[str] = []   # internal Message.id, time-ordered
    subject_norm: str
    participants: list[str] = []   # normalized emails (owner INCLUDED here; L1 strips)
    t_start: datetime
    t_end: datetime
    lineage_conflict: bool = False  # header vs provider disagreement flagged for review
```

## 4. Pipeline overview

```
OAuth grant
   │  A  authorize + fetch (incremental)   §5  — provider-abstracted
   │  B  address normalization             §6
   │  C  thread reconstruction             §7  — headers primary, provider fallback
   │  D  body normalization -> clean_text  §8
   │  E  attachments + links               §9
   │  F  noise classification              §10
   │  G  sensitivity tagging               §11
   │  H  persist + audit (idempotent)      §12
   ▼
Message[] + Thread[]   (consumed by spec 01 §3)
```

## 5. Stage A — auth & fetch  `providers/`

Abstract the provider so Gmail and M365 are interchangeable.

```python
from typing import Protocol, Iterator

class RawMessage:                 # provider-agnostic envelope
    provider_id: str
    provider_thread_id: str
    headers: dict[str, str]       # case-insensitive
    mime_parts: list              # decoded parts (type, charset, bytes)
    labels: list[str]

class MailProvider(Protocol):
    def authorize(self, grant: dict) -> None: ...
    def list_ids(self, since_token: str | None) -> Iterator[str]: ...   # incremental
    def fetch(self, provider_id: str) -> RawMessage: ...
    def sync_token(self) -> str: ...                                    # historyId / delta
```

- **Gmail:** `users.messages.list` + `format=RAW`; incremental via `users.history.list`
  (`historyId`). **M365:** `/me/messages` + `$delta` for incremental.
- **Scope:** request the minimum read-only scope on the single mailbox; time-box the grant.
- **Rate limits:** fetch through a Redis-backed worker queue with exponential backoff on 429;
  checkpoint the sync token after each batch so a crash resumes, not restarts. (S1 runs
  synchronously with no queue — it's a real-ingest concern, wired later; see `docs/decisions.md` D3.)
- **Audit:** append one immutable audit record per ingest run (mailbox, actor, scope, count, ts)
  before any data is read. See §12.

## 6. Stage B — address normalization  `normalize/address.py`

```python
import re
from email.utils import getaddresses
from email.header import decode_header, make_header

PLUS = re.compile(r"\+[^@]*@")

def norm_email(addr: str) -> str:
    addr = addr.strip().lower()
    return PLUS.sub("@", addr)            # strip plus-addressing: a+x@co -> a@co

def decode_mime_words(s: str) -> str:     # RFC 2047 encoded-words in headers
    try: return str(make_header(decode_header(s)))
    except Exception: return s

def parse_addresses(header_value: str) -> list[Address]:
    out = []
    for name, email in getaddresses([header_value or ""]):
        if not email: continue
        out.append(Address(raw=f"{name} <{email}>".strip(),
                           email=norm_email(email),
                           display_names=[decode_mime_words(name)] if name else []))
    return out
```

Note: the local-part is technically case-sensitive per RFC 5321, but in practice every major
provider treats it case-insensitively; we normalize. Keep `raw` for audit if a dispute ever arises.

## 7. Stage C — thread reconstruction  `normalize/threads.py`

Headers are authoritative; the provider thread id is a fallback, never a subject-based merge.

**Policy:**
1. Always union messages linked via `References` / `In-Reply-To`.
2. For a message with **no resolvable header link**, attach it to its provider thread's group.
3. If header lineage and provider grouping disagree (provider merged two header-distinct trees),
   keep the header grouping and set `lineage_conflict=True` for review — do **not** let the
   provider's subject-heuristic merge unrelated mail (this is the "Re: reuse" failure).

```python
class UnionFind:
    def __init__(self): self.p = {}
    def add(self, x): self.p.setdefault(x, x)
    def find(self, x):
        self.add(x)
        while self.p[x] != x: self.p[x] = self.p[self.p[x]]; x = self.p[x]
        return x
    def union(self, a, b): self.p[self.find(a)] = self.find(b)

def norm_mid(s: str | None) -> str | None:
    if not s: return None
    return s.strip().strip("<>").lower()

def parse_refs(headers: dict) -> list[str]:
    refs = (headers.get("References", "") + " " + headers.get("In-Reply-To", "")).split()
    return [norm_mid(r) for r in refs if r.strip()]

def reconstruct(raws: list[RawMessage], owner_email: str) -> tuple[list[Message], list[Thread]]:
    uf = UnionFind(); mid_of = {}; linked = set()
    for r in raws:
        mid = norm_mid(r.headers.get("Message-ID")) or synth_mid(r)
        mid_of[r.provider_id] = mid; uf.add(mid)
        for ref in parse_refs(r.headers):
            uf.union(mid, ref); linked.add(mid)
    # provider fallback ONLY for messages with no header link
    from collections import defaultdict
    prov_groups = defaultdict(list)
    for r in raws: prov_groups[r.provider_thread_id].append(mid_of[r.provider_id])
    for mids in prov_groups.values():
        anchor = mids[0]
        for m in mids[1:]:
            if m not in linked: uf.union(anchor, m)   # attach orphans only
    # group + detect conflicts
    groups = defaultdict(list)
    for r in raws: groups[uf.find(mid_of[r.provider_id])].append(r)
    return materialize_threads(groups, mid_of, prov_groups, owner_email)
```

`synth_mid(r)` = `"synthetic:" + sha256(from + date + subject + body[:200])` for messages whose
source dropped the `Message-ID` header. Synthetic IDs never participate in ref-unioning (they have
no inbound references) and are flagged so citations can note reduced provenance.

## 8. Stage D — body normalization  `normalize/body.py`

```python
import talon; talon.init()
from talon import quotations, signature
from charset_normalizer import from_bytes
import html2text

def pick_part(raw: RawMessage):
    plains = [p for p in raw.mime_parts if p.type == "text/plain"]
    if plains: return plains[0], "plain"
    htmls = [p for p in raw.mime_parts if p.type == "text/html"]
    if htmls: return htmls[0], "html"
    return None, None

def decode(part) -> str:
    return str(from_bytes(part.bytes).best() or part.bytes.decode("utf-8", "replace"))

def clean_body(raw: RawMessage, sender_name: str) -> str:
    part, kind = pick_part(raw)
    if part is None: return ""
    text = decode(part)
    if kind == "html":
        h = html2text.HTML2Text(); h.ignore_links = False; h.body_width = 0
        text = h.handle(text)
    text = quotations.extract_from_plain(text)         # drop quoted reply chain
    text, _sig = signature.extract(text, sender=sender_name)  # drop signature
    return " ".join(text.split())                       # normalize whitespace
```

Encrypted / signed bodies (`application/pkcs7-mime`, PGP) can't be cleaned: store an empty
`clean_text`, tag `PRIVILEGED` defensively, and keep the raw archived. Calendar parts
(`text/calendar`) are extracted as structured invite metadata, not free text (feeds L1 events).

## 9. Stage E — attachments & links  `normalize/artifacts.py`

```python
import hashlib, re, tldextract
URL = re.compile(r"https?://[^\s>)\]]+")

def hash_attachment(part) -> AttachmentRef:
    return AttachmentRef(sha256=hashlib.sha256(part.bytes).hexdigest(),
                         filename=part.filename, mimetype=part.type, size_bytes=len(part.bytes))

def link_domains(text: str) -> list[str]:
    out = set()
    for u in URL.findall(text):
        ext = tldextract.extract(u)
        if ext.registered_domain: out.add(ext.registered_domain)
    return sorted(out)
```

Default: attachment **content is not retained** — only the sha256 (used for clustering's
shared-artifact signal and dedupe) plus filename/type/size. Retaining content is an explicit,
audited opt-in.

## 10. Stage F — noise classification  `normalize/noise.py`

High-precision, header-driven (we'd rather keep a borderline human email than drop one).

```python
import re
BULK_PRECEDENCE = {"bulk", "list", "junk"}
NOREPLY = re.compile(r"(no[-_.]?reply|donotreply|do[-_.]?not[-_.]?reply|notifications?|mailer)@")
ESP = ("mailchimp", "sendgrid", "marketo", "sparkpost", "amazonses", "constantcontact")

def is_noise(raw: RawMessage, sender_email: str) -> bool:
    h = {k.lower(): v for k, v in raw.headers.items()}
    if "list-unsubscribe" in h: return True
    if h.get("precedence", "").lower() in BULK_PRECEDENCE: return True
    if h.get("auto-submitted", "no").lower() != "no": return True
    if NOREPLY.search(sender_email): return True
    if any(e in h.get("x-mailer", "").lower() for e in ESP): return True
    return False
```

Secondary signal (computed after threads exist): a sender whose threads **never** receive an
owner reply and that matches ≥1 weak heuristic is escalated to noise. Log all noise decisions so
L0 thresholds can be tuned and so false drops are recoverable.

## 11. Stage G — sensitivity tagging  `normalize/sensitivity.py`

A v1 rules floor; a learned classifier is the upgrade (§22). Tags drive exclusion/redaction
downstream (default: tagged content is excluded from clustering & synthesis).

```python
PRIVILEGE = ["attorney-client", "privileged and confidential", "legal hold", "outside counsel"]
HR = ["compensation", "salary", "performance review", "performance improvement plan",
      "termination", "severance", "offer letter", "benefits enrollment"]
# D7: bare "pip" replaced with "performance improvement plan" — bare short acronyms
# false-match in engineering mailboxes ("pip install ..."); word-boundary regex applied
# to ALL keywords (see docs/decisions.md D7).

def tag_sensitivity(msg: Message, cfg) -> list[Sensitivity]:
    import re

    def _build_matcher(keywords):
        alt = "|".join(re.escape(k) for k in keywords)
        return re.compile(rf"\b(?:{alt})\b", re.IGNORECASE)

    blob = f"{msg.subject} {msg.clean_text}"  # do NOT lowercase before matching
    sender = msg.sender.email
    if _build_matcher(PRIVILEGE).search(blob) or domain_in(sender, cfg.legal_domains):
        tags.add(Sensitivity.PRIVILEGED)
    if domain_in(sender, cfg.legal_domains): tags.add(Sensitivity.LEGAL)
    if _build_matcher(HR).search(blob) or sender in cfg.hr_senders: tags.add(Sensitivity.HR)
    if is_personal_domain(sender, cfg) and not looks_work_related(blob):
        tags.add(Sensitivity.PERSONAL)
    return sorted(tags) or [Sensitivity.NONE]
```

`cfg` (per-tenant): `legal_domains`, `hr_senders`, `personal_domains`, owner org domains. Rules are
intentionally conservative on recall — a missed tag is a follow-on review item; a false redaction
hides real work. Track both rates against a labeled sample.

## 12. Stage H — persistence & audit  `store.py`

- **Idempotent upsert** keyed on `message_id_header`; recompute a per-message content hash and skip
  unchanged rows. Raw MIME → object store at `raw_uri`; structured `Message`/`Thread` → Postgres.
- **Audit log** (append-only, separate table/stream): one row per run with mailbox id, OAuth subject,
  granted scope, message count, started/finished timestamps, sync token. Never mutated or deleted.
- **Retention**: configurable TTL on structured output after a coverage period ends (§17).

## 13. Idempotency & incremental sync

- **Gmail:** persist `historyId`; on the next run pull only `history.list` deltas. **M365:** persist
  the `$delta` token. New/changed messages flow through B–H; the dedupe key makes re-seen messages no-ops.
- A full re-ingest (e.g. after a normalization-logic change) is safe: same inputs → same `message_id_header`
  keys → upsert overwrites in place, no duplicates, thread ids stable via the same reconstruction.

## 14. Parameters & defaults  `params.py`

| Param | Default | Effect |
|---|---|---|
| `scope` | read-only single mailbox | Least privilege; never request write/delete. |
| `batch_size` | 100 | Messages per fetch batch (provider page size). |
| `max_backoff_s` | 64 | 429 retry ceiling. |
| `clean_text_max_chars` | 50000 | Truncate giant bodies before storage/embedding. |
| `attachment_retain` | false | Keep attachment content (audited opt-in) vs hash-only. |
| `noise_reply_ratio` | 0.0 | Sender with ≤ this owner-reply ratio + a weak signal → noise. |
| `sensitivity_mode` | exclude | `exclude` \| `redact` \| `tag-only` for tagged content downstream. |
| `retention_days` | null | TTL on structured output post-coverage (null = tenant policy). |

## 15. Edge cases & failure modes

| Case | Handling |
|---|---|
| Missing `Message-ID` | Synthesize (`synth_mid`); flag reduced provenance; no ref-unioning. |
| Malformed `References` | Parse leniently; ignore unparseable tokens. |
| Provider merges unrelated mail (subject reuse) | Header grouping wins; set `lineage_conflict`. |
| Same message in multiple folders/labels | Deduped by `message_id_header`. |
| Encrypted / signed body | Empty `clean_text`, tag `PRIVILEGED`, keep raw. |
| Non-UTF8 / mojibake | `charset-normalizer` best-guess; replace undecodable bytes. |
| HTML-only marketing mail | Usually caught by §10; if not, html2text still yields clean text. |
| Calendar invite | Parsed as structured invite metadata (feeds L1 events), not body text. |
| Huge thread / message | `clean_text` truncated to `clean_text_max_chars`; raw retained whole. |
| OAuth token expiry mid-run | Refresh; if refused, checkpoint sync token and surface re-auth. |

## 16. Privacy & compliance (this is the layer where it lives)

- **Third-party data:** the mailbox holds personal data of external contacts who never consented to
  processing; this is the layer that must support data-subject access/deletion and per-tenant config.
- **Sensitive content:** §11 tags privileged/legal/HR/personal; default `sensitivity_mode=exclude`.
- **Least privilege:** read-only, single-mailbox, time-boxed scope (§5).
- **Audit:** immutable per-run log (§12); every access is attributable.
- **Token security:** OAuth tokens are the crown-jewel credential — stored only in a secrets manager,
  never in the app DB or logs.
- *Not legal advice — a privacy review precedes any regulated-buyer deployment.*

## 17. Module layout & CLI

```
services/ingest/
  providers/
    base.py          # MailProvider protocol, RawMessage
    gmail.py         # Gmail API impl
    msgraph.py       # Microsoft Graph impl
    fixture.py       # dev/test provider: yields RawMessage from fixtures/mailbox.json (see decisions D4)
  normalize/
    address.py       # §6
    threads.py       # §7
    body.py          # §8
    artifacts.py     # §9
    noise.py         # §10
    sensitivity.py   # §11
  store.py           # §12 persistence + audit
  params.py          # §14
  pipeline.py        # orchestrator: authorize -> fetch -> normalize -> persist
```

CLI: `python -m ingest.pipeline --mailbox <id> --provider gmail [--full | --since <token>]`

## 18. Observability

- Per-run metrics: fetched, deduped, noise-flagged, sensitivity-tagged counts; `lineage_conflict`
  count; wall-time per stage; sync token.
- Sample dumps (gated): N cleaned bodies before/after for eyeballing quote/signature stripping.
- Alerts: noise rate way off baseline (filter drift), spike in synthetic Message-IDs (provider/parse
  issue), repeated 429s (rate-limit tuning).

## 19. Acceptance / Definition of Done

- [ ] Gmail and M365 providers both implement `MailProvider`; pipeline is provider-agnostic.
- [ ] All §2 invariants enforced; re-ingest of an unchanged mailbox is a verified no-op.
- [ ] Thread reconstruction passes fixtures incl. subject-reuse (no false merge) and dropped-`Message-ID`.
- [ ] `clean_text` quote/signature stripping verified on a labeled sample (precision target ≥ 0.9 on
      "no quoted text leaked").
- [ ] Noise + sensitivity classifiers evaluated against a labeled sample with reported precision/recall.
- [ ] Incremental sync via `historyId`/`$delta` works and resumes after a forced crash.
- [ ] Audit log written before data access; OAuth tokens never touch app DB or logs.

## 20. Sprint task breakdown (`@sprint S1`, L0 half)

| # | Ticket | Depends on | Done when |
|---|---|---|---|
| 0.1 | `providers/base.py` + Gmail provider (auth, list, fetch, history) | OAuth app reg | raw fetch + incremental on a test mailbox |
| 0.2 | M365 provider (`$delta`) | 0.1 | parity with Gmail provider |
| 0.3 | `normalize/address.py` + tests | — | RFC 2047 + plus-addressing handled |
| 0.4 | `normalize/threads.py` (UF + conflict policy) + fixtures | 0.1 | subject-reuse + dropped-id fixtures pass |
| 0.5 | `normalize/body.py` (talon + html2text + charset) | 0.1 | quote/sig stripping sample verified |
| 0.6 | `normalize/artifacts.py` (hashes + PSL domains) | 0.1 | attachment refs + link domains emitted |
| 0.7 | `normalize/noise.py` + labeled eval | 0.5 | precision/recall reported |
| 0.8 | `normalize/sensitivity.py` + config + labeled eval | 0.5 | tags + redaction modes work |
| 0.9 | `store.py` (idempotent upsert, raw archive, audit) | 0.3–0.8 | re-ingest no-op verified |
| 0.10 | `pipeline.py` + CLI + metrics | all above | end-to-end run green; hands off to spec 01 §3 |

## 21. Open decisions

- ~~Provider priority for v1~~ — **resolved (D2):** Gmail first; M365 stubbed. See `docs/decisions.md`.
- Object store choice for raw MIME (S3-compatible) and whether raw is retained long-term or TTL'd.
  (S1: `raw_uri = None`, no object store — D6.)
- ~~Secrets manager for OAuth tokens~~ — **resolved for S1 (D6):** env vars behind a `get_token()`
  interface; secrets manager is production hardening. Tokens never touch DB/logs. See `docs/decisions.md`.
- Learned sensitivity classifier: when to graduate from rules, and what labeled data we can use.
- Calendar/invite handling depth in v1 (metadata only vs full event extraction — coordinate with L1 §7).
