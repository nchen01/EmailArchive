"""L1 event extraction (spec 01 §7, S4 ticket 4.1).

Pure orchestration around an **injectable** ``extract_fn`` — the only
nondeterministic dependency — exactly mirroring how clustering injects
``embed_fn``/``nlp`` (D10). Tests pass a deterministic fake; production passes
the Anthropic-backed fn (``events_llm.py``). The network call never touches this
module, which keeps 4.1 fully testable offline.

Responsibilities here (everything *except* the model call):
- Sensitivity gate (exclude threads with any non-NONE message).
- Build per-thread context for ``extract_fn``.
- Actor resolution via ``email_to_person_id`` (skip if unresolvable).
- Owner-as-sole-actor filtering (skip unless directly evidenced).
- Citation enforcement (>=1 source_message_id_header; skip if empty).
- project_id attachment from the primary ThreadProjectAssignment.
- Construct validated ``ekc_schemas.Event`` rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
from uuid import uuid4

from ekc_schemas import (
    Event,
    EventType,
    Message,
    Sensitivity,
    Thread,
    ThreadProjectAssignment,
)


# ── params ───────────────────────────────────────────────────────────────────

@dataclass
class EventParams:
    """Event-extraction tuning knobs (spec 01 §7)."""
    # Confidence assigned to an extracted event when the model returns none, and
    # the (lower) confidence used when the owner is the sole identifiable actor.
    default_confidence: float = 0.6
    owner_sole_actor_confidence: float = 0.3
    sensitivity_mode: str = "exclude"  # only "exclude" supported in S4


# ── extract_fn input/output contracts ────────────────────────────────────────

@dataclass
class ThreadMessageContext:
    """One message of a thread, as handed to ``extract_fn``."""
    message_id_header: str
    sender_email: str
    ts: str
    clean_text: str


@dataclass
class ThreadContext:
    """The per-thread payload handed to ``extract_fn`` (spec 01 §7: per thread)."""
    thread_id: str
    subject: str
    messages: list[ThreadMessageContext] = field(default_factory=list)

    @property
    def message_id_headers(self) -> set[str]:
        return {m.message_id_header for m in self.messages}


@dataclass
class ExtractedEventRaw:
    """Raw, pre-resolution event as returned by ``extract_fn``.

    ``actor_name_or_email`` is mapped to a person_id here; ``type`` is an
    EventType value; ``source_message_id_headers`` hold message_id_header
    citations (NOT internal ids).
    """
    actor_name_or_email: str
    type: str
    summary: str
    source_message_id_headers: list[str]
    confidence: float | None = None


# An extract_fn takes a ThreadContext and returns raw events for that thread.
ExtractFn = Callable[[ThreadContext], list[ExtractedEventRaw]]


# ── helpers ──────────────────────────────────────────────────────────────────

def _thread_is_sensitive(messages: list[Message]) -> bool:
    """A thread is excluded if any message carries a non-NONE sensitivity tag."""
    for m in messages:
        for s in m.sensitivity:
            if s != Sensitivity.NONE:
                return True
    return False


def _build_context(thread: Thread, messages: list[Message]) -> ThreadContext:
    msgs = sorted(messages, key=lambda m: m.ts)
    return ThreadContext(
        thread_id=thread.id,
        subject=thread.subject_norm,
        messages=[
            ThreadMessageContext(
                message_id_header=m.message_id_header,
                sender_email=m.sender.email,
                ts=m.ts.isoformat(),
                clean_text=m.clean_text,
            )
            for m in msgs
        ],
    )


def _resolve_actor(
    actor_name_or_email: str,
    email_to_person_id: dict[str, str],
    ctx: ThreadContext,
) -> str | None:
    """Map an actor mention to a person_id, or None if unresolvable.

    Resolution order:
    1. Direct email match (lowercased) in ``email_to_person_id``.
    2. The mention is a name that uniquely matches one sender email in the
       thread (local-part / display heuristic) → that sender's person_id.
    Anything ambiguous returns None (skip the event — precision over recall).
    """
    key = actor_name_or_email.strip().lower()
    if not key:
        return None

    # 1. exact email.
    if key in email_to_person_id:
        return email_to_person_id[key]

    # 2. resolve a bare name against the thread's senders. Match on the email
    #    local-part or any token of the name appearing in the local-part.
    name_tokens = [t for t in key.replace(".", " ").split() if t]
    candidates: set[str] = set()
    for m in ctx.messages:
        sender = m.sender_email.lower()
        if sender not in email_to_person_id:
            continue
        local = sender.split("@", 1)[0]
        local_tokens = set(local.replace(".", " ").replace("_", " ").split())
        if key == sender:
            candidates.add(email_to_person_id[sender])
        elif name_tokens and any(t in local_tokens or t in local for t in name_tokens):
            candidates.add(email_to_person_id[sender])

    if len(candidates) == 1:
        return next(iter(candidates))
    return None


def _evidenced_in_text(raw: ExtractedEventRaw, ctx: ThreadContext) -> bool:
    """Heuristic: the action is directly evidenced if any cited message exists
    and carries non-empty text (the model claims to draw the event from it)."""
    cited = set(raw.source_message_id_headers)
    for m in ctx.messages:
        if m.message_id_header in cited and m.clean_text.strip():
            return True
    return False


# ── orchestration ────────────────────────────────────────────────────────────

def extract_events(
    threads: list[Thread],
    messages_by_thread: dict[str, list[Message]],
    assignments: list[ThreadProjectAssignment],
    email_to_person_id: dict[str, str],
    owner_person_id: str,
    *,
    extract_fn: ExtractFn,
    params: EventParams | None = None,
) -> list[Event]:
    """Extract grounded ``Event`` rows from threads (spec 01 §7, D10).

    Per-thread, sensitivity-gated. The LLM is injected as ``extract_fn``; this
    function never performs a network call.
    """
    if params is None:
        params = EventParams()

    # Primary project per thread (is_primary == True).
    primary_project: dict[str, str] = {
        a.thread_id: a.project_id for a in assignments if a.is_primary
    }

    # Normalize the email->person map to lowercased keys for robust matching.
    e2p = {k.lower(): v for k, v in email_to_person_id.items()}

    events: list[Event] = []
    for thread in threads:
        msgs = messages_by_thread.get(thread.id, [])
        if not msgs:
            continue
        # Sensitivity gate — excluded threads produce zero events.
        if _thread_is_sensitive(msgs):
            continue

        ctx = _build_context(thread, msgs)
        valid_headers = ctx.message_id_headers
        raws = extract_fn(ctx)

        for raw in raws:
            # a. resolve actor; skip if unresolvable.
            actor_pid = _resolve_actor(raw.actor_name_or_email, e2p, ctx)
            if actor_pid is None:
                continue

            # c. citation enforcement: keep only citations that belong to this
            #    thread; require >=1.
            cited = [h for h in raw.source_message_id_headers if h in valid_headers]
            if not cited:
                continue
            raw = ExtractedEventRaw(
                actor_name_or_email=raw.actor_name_or_email,
                type=raw.type,
                summary=raw.summary,
                source_message_id_headers=cited,
                confidence=raw.confidence,
            )

            # b. owner-as-sole-actor filtering. The owner is in (almost) every
            #    thread and carries no discriminative signal: drop the event
            #    when the owner is the sole identifiable actor and the action is
            #    not directly evidenced in the cited text; otherwise lower its
            #    confidence (owner-as-actor inflation risk).
            confidence = (
                raw.confidence
                if raw.confidence is not None
                else params.default_confidence
            )
            if actor_pid == owner_person_id:
                if not _evidenced_in_text(raw, ctx):
                    continue
                confidence = min(confidence, params.owner_sole_actor_confidence)

            # type validation (skip silently on an unknown label).
            try:
                etype = EventType(raw.type)
            except ValueError:
                continue

            # d. attach primary project_id (None if unassigned).
            project_id = primary_project.get(thread.id)

            # e. construct the validated Event.
            events.append(
                Event(
                    id=str(uuid4()),
                    actor_person_id=actor_pid,
                    type=etype,
                    summary=raw.summary.strip(),
                    project_id=project_id,
                    source_message_ids=cited,
                    confidence=confidence,
                )
            )

    return events


def get_processed_headers(
    threads: list[Thread],
    messages_by_thread: dict[str, list[Message]],
) -> list[str]:
    """All message_id_header values from threads that extraction would process.

    This is the delete scope for persist_events: even when extract_fn returns []
    for a thread (or the model produces no valid events), old Events citing those
    messages must be cleared. Mirrors the sensitivity gate in extract_events so
    the two functions stay in sync.
    """
    headers: list[str] = []
    for thread in threads:
        msgs = messages_by_thread.get(thread.id, [])
        if msgs and not _thread_is_sensitive(msgs):
            headers.extend(m.message_id_header for m in msgs)
    return headers
