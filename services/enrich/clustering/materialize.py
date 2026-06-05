"""Stage G — materialize projects (spec 03 §11).

Correction A (binding): project ids are deterministic ``uuid5`` over a canonical
cluster signature (sorted thread_ids), NOT ``uuid4`` (spec bug) and NOT Python's
randomized ``hash()``. Re-running on identical input yields identical ids.

We also return a ``community -> project_id`` map so confidence (§13) can find the
right community profile without reconstructing it (per the §15 implementation
note).
"""
from __future__ import annotations

import uuid
from collections import defaultdict

_NS = uuid.NAMESPACE_DNS


def _stable_project_id(thread_ids: list[str]) -> str:
    key = "project:" + ",".join(sorted(thread_ids))
    return str(uuid.uuid5(_NS, key))


def materialize(feats, assigns: dict):
    """Return ``(projects, assignments, comm_to_pid)``.

    ``projects`` and ``assignments`` are plain dicts (later validated into
    ekc_schemas models by the pipeline). Members are sorted by involvement desc,
    then person_id for stable ties. Thread ids within a project are sorted.
    """
    by_c: dict = defaultdict(list)  # community -> [(thread_idx, weight, is_primary)]
    for i in sorted(assigns):
        cs = assigns[i]
        primary = max(cs, key=lambda c: (cs[c], -c))
        for c, w in cs.items():
            by_c[c].append((i, w, c == primary))

    projects: list[dict] = []
    assignments: list[dict] = []
    comm_to_pid: dict = {}

    for c in sorted(by_c):
        rows = sorted(by_c[c], key=lambda r: feats[r[0]].thread_id)
        thread_ids = [feats[i].thread_id for i, _, _ in rows]
        pid = _stable_project_id(thread_ids)
        comm_to_pid[c] = pid

        members: dict = defaultdict(lambda: [0.0, 0])  # person -> [involvement, msgs]
        t_lo = t_hi = None
        for i, w, is_primary in rows:
            f = feats[i]
            t_lo = f.t_start if t_lo is None else min(t_lo, f.t_start)
            t_hi = f.t_end if t_hi is None else max(t_hi, f.t_end)
            for p in sorted(f.msg_count_by_person):
                mc = f.msg_count_by_person[p]
                members[p][0] += w * mc
                members[p][1] += mc
            assignments.append(
                dict(
                    thread_id=f.thread_id,
                    project_id=pid,
                    weight=round(float(w), 4),
                    is_primary=bool(is_primary),
                )
            )

        member_list = [
            dict(person_id=p, involvement=round(members[p][0], 2), message_count=members[p][1])
            for p in sorted(members, key=lambda p: (-members[p][0], p))
        ]
        projects.append(
            dict(
                id=pid,
                member_ids=[m["person_id"] for m in member_list],
                members=member_list,
                thread_ids=thread_ids,
                start=t_lo,
                end=t_hi,
            )
        )

    # Stable output ordering: by project id.
    projects.sort(key=lambda p: p["id"])
    assignments.sort(key=lambda a: (a["thread_id"], a["project_id"]))
    return projects, assignments, comm_to_pid
