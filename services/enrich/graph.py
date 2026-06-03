"""L1 §4 — relationship graph. One Edge per contact; owner is implicit.

Weight blends volume, recency, reciprocity. Deterministic: ``now`` is a fixed
parameter, never wall-clock. Noise messages are skipped.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from ekc_schemas import Edge

from .params import EnrichParams

_DEFAULT_NOW = datetime(2026, 6, 3, tzinfo=timezone.utc)


def _bump(counters: dict, pid: str, *, sent_to: int = 0, received: int = 0, ts: datetime) -> None:
    c = counters.get(pid)
    if c is None:
        c = {"sent_to": 0, "received": 0, "first": ts, "last": ts}
        counters[pid] = c
    c["sent_to"] += sent_to
    c["received"] += received
    if ts < c["first"]:
        c["first"] = ts
    if ts > c["last"]:
        c["last"] = ts


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _edge_weight(e: Edge, now: datetime, params: EnrichParams) -> float:
    days = (now - _aware(e.last_contact)).days
    volume = math.log1p(e.message_count)
    recency = math.exp(-days / params.edge_half_life_days)
    recip = 1 - abs(e.sent_to_count - e.received_count) / max(e.message_count, 1)
    return round(
        params.volume_weight * volume
        + params.recency_weight * recency
        + params.recip_weight * recip,
        4,
    )


def build_relationship_graph(
    messages: list,
    owner_email: str,
    email_to_person_id: dict[str, str],
    params: EnrichParams,
    now: datetime | None = None,
) -> list[Edge]:
    if now is None:
        now = _DEFAULT_NOW

    owner_person_id = email_to_person_id.get(owner_email)
    counters: dict[str, dict] = {}

    for msg in messages:
        if msg.noise:
            continue
        sender_pid = email_to_person_id.get(msg.sender.email)
        recipient_pids = {
            email_to_person_id.get(a.email) for a in (msg.to + msg.cc)
        } - {None}

        if sender_pid == owner_person_id and owner_person_id is not None:
            for rpid in recipient_pids:
                if rpid != owner_person_id:
                    _bump(counters, rpid, sent_to=1, ts=_aware(msg.ts))
        elif sender_pid is not None and owner_person_id in recipient_pids:
            _bump(counters, sender_pid, received=1, ts=_aware(msg.ts))

    edges: list[Edge] = []
    for person_id, c in counters.items():
        e = Edge(
            person_id=person_id,
            message_count=c["sent_to"] + c["received"],
            sent_to_count=c["sent_to"],
            received_count=c["received"],
            first_contact=c["first"],
            last_contact=c["last"],
            weight=0.0,
        )
        e = e.model_copy(update={"weight": _edge_weight(e, now, params)})
        edges.append(e)

    edges.sort(key=lambda e: e.person_id)
    return edges
