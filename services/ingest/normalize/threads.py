"""Stage C — thread reconstruction (spec 00 §7) + message/thread materialization.

Headers are authoritative; the provider thread id is a fallback, never a
subject-based merge. The subtle case: two messages sharing a provider_thread_id
but with no header lineage between them must stay SEPARATE threads, both flagged
``lineage_conflict=True``.

Algorithm (per the S1 brief):
  1. Normalize each message's Message-ID (or synthesize one).
  2. UnionFind over message ids, unioning with References / In-Reply-To values.
  3. Group by provider_thread_id; if a provider group spans 2+ UF roots, flag all
     resulting threads as lineage_conflict and DO NOT union across those roots.
  4. Each UF root = one Thread.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from collections import defaultdict
from email.header import decode_header as _decode_header
from datetime import datetime, timezone

from ekc_schemas import Message, Sensitivity, Thread

from ..params import IngestParams
from ..providers.base import RawMessage
from .address import norm_email, parse_addresses
from .artifacts import extract_link_domains, process_attachments
from .body import clean_body_from_raw
from .noise import is_noise
from .sensitivity import tag_sensitivity

_NS = uuid.NAMESPACE_DNS


# ── UnionFind ────────────────────────────────────────────────────────────────
class UnionFind:
    def __init__(self):
        self.p: dict[str, str] = {}

    def add(self, x: str) -> None:
        self.p.setdefault(x, x)

    def find(self, x: str) -> str:
        self.add(x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str) -> None:
        self.p[self.find(a)] = self.find(b)


# ── helpers ──────────────────────────────────────────────────────────────────
def norm_mid(s: str | None) -> str | None:
    if not s:
        return None
    return s.strip().strip("<>").lower()


def parse_refs(headers: dict[str, str]) -> list[str]:
    refs = (headers.get("References", "") + " " + headers.get("In-Reply-To", "")).split()
    return [m for r in refs if r.strip() and (m := norm_mid(r))]


def synth_mid(raw: RawMessage) -> str:
    h = raw.headers
    body_first_200 = b""
    for part in raw.mime_parts:
        if part.type == "text/plain":
            body_first_200 = part.bytes[:200]
            break
    payload = (
        f"{h.get('From', '')}|{h.get('Date', '')}|{h.get('Subject', '')}|"
    ).encode("utf-8") + body_first_200
    return "synthetic:" + hashlib.sha256(payload).hexdigest()


def _parse_ts(date_header: str) -> datetime:
    from email.utils import parsedate_to_datetime

    if not date_header:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        dt = parsedate_to_datetime(date_header)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(date_header.replace("Z", "+00:00"))
        except ValueError:
            return datetime(1970, 1, 1, tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


_SUBJECT_PREFIX = re.compile(r"^\s*(re|fwd?|fw)\s*:\s*", re.IGNORECASE)


def decode_mime_words(value: str) -> str:
    """Decode an RFC 2047 encoded-word header value to a Unicode string.

    Handles B-encoding (base64) and Q-encoding (quoted-printable) with any
    charset, and mixed messages like 'prefix =?utf-8?b?...?= suffix'.
    Consecutive encoded words are joined without an inserted space (RFC 2047
    §6.2 rule). Falls back to the raw value on any decode error.

    Examples:
        '=?utf-8?b?4oCU?='                                    -> '—'
        'INCIDENT P1: p99 =?utf-8?b?4oCU?= triaging'          -> 'INCIDENT P1: p99 — triaging'
        '=?US-ASCII?Q?View_Your_New_Benefit_Amount?='          -> 'View Your New Benefit Amount'
    """
    if not value or "=?" not in value:
        return value
    try:
        parts = _decode_header(value)
        out: list[str] = []
        for raw, charset in parts:
            if isinstance(raw, bytes):
                out.append(raw.decode(charset or "utf-8", errors="replace"))
            else:
                out.append(raw)
        return "".join(out)
    except Exception:
        return value


def _norm_subject(subject: str) -> str:
    s = subject or ""
    prefix = _SUBJECT_PREFIX
    while True:
        new = prefix.sub("", s)
        if new == s:
            break
        s = new
    return s.strip().lower()


def _message_uuid(message_id_header: str, db_mailbox_id: str = "") -> str:
    """Stable Message PK.

    When ``db_mailbox_id`` is provided (real DB persisted run), the ID is
    scoped to that mailbox so that two mailboxes containing the same RFC
    Message-ID produce different PKs and can coexist without collision.
    When empty (in-memory fixture/test runs) the legacy scheme is used so
    existing in-memory tests need no changes.
    """
    if db_mailbox_id:
        return str(uuid.uuid5(_NS, f"msg:{db_mailbox_id}:{message_id_header}"))
    return str(uuid.uuid5(_NS, "msg:" + message_id_header))


def _thread_uuid(root_mid: str, db_mailbox_id: str = "") -> str:
    """Stable Thread PK — same mailbox-scoping rationale as _message_uuid."""
    if db_mailbox_id:
        return str(uuid.uuid5(_NS, f"thread:{db_mailbox_id}:{root_mid}"))
    return str(uuid.uuid5(_NS, root_mid))


# ── reconstruction ───────────────────────────────────────────────────────────
def reconstruct(
    raws: list[RawMessage],
    owner_email: str,
    params: IngestParams,
    db_mailbox_id: str = "",
) -> tuple[list[Message], list[Thread]]:
    uf = UnionFind()
    mid_of: dict[str, str] = {}          # provider_id -> normalized mid

    for r in raws:
        mid = norm_mid(r.headers.get("Message-ID")) or synth_mid(r)
        mid_of[r.provider_id] = mid
        uf.add(mid)
        # Synthetic mids never participate in ref-unioning (no inbound refs).
        if not mid.startswith("synthetic:"):
            for ref in parse_refs(r.headers):
                uf.union(mid, ref)

    # Group by provider thread id; detect 2+ roots → lineage conflict.
    prov_groups: dict[str, list[str]] = defaultdict(list)
    for r in raws:
        prov_groups[r.provider_thread_id].append(mid_of[r.provider_id])

    conflicted_roots: set[str] = set()
    for mids in prov_groups.values():
        roots = {uf.find(m) for m in mids}
        if len(roots) >= 2:
            # 2+ roots: keep separate, flag each root's thread as conflicted.
            conflicted_roots |= roots
        # 1 root: no conflict, nothing to union (already linked via headers).

    # Build per-root message lists.
    root_to_raws: dict[str, list[RawMessage]] = defaultdict(list)
    for r in raws:
        root_to_raws[uf.find(mid_of[r.provider_id])].append(r)

    # Materialize.
    messages: list[Message] = []
    threads: list[Thread] = []
    msg_by_provider: dict[str, Message] = {}

    for root, group_raws in root_to_raws.items():
        # Sort messages chronologically.
        decorated = sorted(
            group_raws, key=lambda r: (_parse_ts(r.headers.get("Date", "")), mid_of[r.provider_id])
        )
        thread_id = _thread_uuid(root, db_mailbox_id)
        thread_messages: list[Message] = []
        provider_thread_ids: list[str] = []
        participants: set[str] = set()

        for r in decorated:
            mid = mid_of[r.provider_id]
            sender_addrs = parse_addresses(r.headers.get("From", ""))
            sender = sender_addrs[0] if sender_addrs else parse_addresses("unknown@unknown")[0]
            to_addrs = parse_addresses(r.headers.get("To", ""))
            cc_addrs = parse_addresses(r.headers.get("Cc", ""))
            ts = _parse_ts(r.headers.get("Date", ""))
            subject = decode_mime_words(r.headers.get("Subject", "") or "")

            clean_text = clean_body_from_raw(r, sender.display_names[0] if sender.display_names else None,
                                             params.clean_text_max_chars)
            attachment_refs = process_attachments(r)
            link_domains = extract_link_domains(clean_text)
            noise = is_noise(r, sender.email)
            sensitivity = tag_sensitivity(subject, clean_text, sender.email, params)

            msg = Message(
                id=_message_uuid(mid, db_mailbox_id),
                message_id_header=mid,
                provider_id=r.provider_id,
                thread_id=thread_id,
                sender=sender,
                to=to_addrs,
                cc=cc_addrs,
                ts=ts,
                subject=subject,
                clean_text=clean_text,
                attachment_refs=attachment_refs,
                link_domains=link_domains,
                sensitivity=sensitivity,
                noise=noise,
                raw_uri=None,
            )
            thread_messages.append(msg)
            messages.append(msg)
            msg_by_provider[r.provider_id] = msg

            if r.provider_thread_id and r.provider_thread_id not in provider_thread_ids:
                provider_thread_ids.append(r.provider_thread_id)
            participants.add(sender.email)
            for a in to_addrs + cc_addrs:
                participants.add(a.email)

        earliest = thread_messages[0]
        root_header = next(
            (m.message_id_header for m in thread_messages
             if not m.message_id_header.startswith("synthetic:")),
            None,
        )
        thread = Thread(
            id=thread_id,
            provider_thread_ids=provider_thread_ids,
            root_message_id_header=root_header,
            message_ids=[m.id for m in thread_messages],
            subject_norm=_norm_subject(earliest.subject),
            participants=sorted(participants),
            t_start=min(m.ts for m in thread_messages),
            t_end=max(m.ts for m in thread_messages),
            lineage_conflict=root in conflicted_roots,
        )
        threads.append(thread)

    # Deterministic ordering of output.
    threads.sort(key=lambda t: (t.t_start, t.id))
    messages.sort(key=lambda m: (m.ts, m.id))
    return messages, threads
