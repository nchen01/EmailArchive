"""Relationship-map derivation (S13).

Derives a graph-backed, tree-renderable relationship map for a mailbox *live*
from existing L1 tables (Person/Identity/Thread/Message/Edge/Org/Project/
ProjectMember/ThreadProjectAssignment). Nothing here is persisted — see the note
in ``derive.py`` for when a materialized ``relationship_edge`` table may become
necessary.

Privacy posture (unchanged from the rest of the pipeline): a thread is eligible
only if it is non-noise and contains NO sensitive message; if any message in a
thread carries a non-``{none}`` sensitivity tag the whole thread is excluded.
Evidence references use ``message_id_header`` / thread ids / project ids — never
raw bodies. Edge weight is communication volume / evidence count, never a claim
of importance.
"""
