"""Tunable parameters for relationship-map derivation (S13).

Nothing hardcoded in logic — all thresholds live here (AGENTS.md convention).
None of these introduce wall-clock time: staleness/recency are measured relative
to a *derived* "now" (the latest message/thread timestamp in the mailbox), so
output stays deterministic for identical DB state.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RelationshipParams:
    # An edge below this evidence count is dropped entirely (Q6: hide only below a
    # minimum evidence threshold). 1 = keep anything with at least one piece of
    # evidence.
    min_evidence: int = 1

    # An edge whose weight is below this is kept but rendered muted ("weak"),
    # alongside its evidence count and type (Q6).
    weak_weight_threshold: float = 0.20

    # A relationship whose last activity is older than this many days (relative to
    # the derived mailbox "now") is rendered muted ("stale"), and can be filtered
    # out with the recency_days query param (Q9).
    stale_days: int = 180

    # "Bridge contact" = a person assigned to at least this many projects (Q8).
    bridge_min_projects: int = 2

    # Cap on message_id_header citations carried per edge, to bound payload size.
    max_source_message_ids: int = 25

    # Cap on nodes/edges returned in graph mode, to keep the payload demo-sized.
    max_graph_nodes: int = 300
    max_graph_edges: int = 800


DEFAULT_PARAMS = RelationshipParams()
