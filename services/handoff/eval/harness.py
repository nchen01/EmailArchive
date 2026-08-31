"""Seed a scenario, run the real generator, and evaluate it against gold (S43).

Offline + deterministic. Requires a local ``DATABASE_URL`` (Postgres) to run the
generator; it seeds a THROWAWAY mailbox, runs ``generate_candidate``, reads back the
``handoff_*`` snapshot rows, evaluates, and tears the mailbox down. No network, no
external API. ``evaluate()`` is a pure function over collected data and needs no DB.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select

from services.db import models as orm
from services.handoff.coverage_contract import build_coverage_contract
from services.handoff.generator import generate_candidate
from services.handoff.safety import high_severity, scan_package

_TS = datetime(2026, 4, 15, tzinfo=timezone.utc)


# -- seeding (throwaway mailbox) -----------------------------------------------

def _parse_date(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def seed_scenario(session, data: dict) -> tuple[str, str]:
    """Seed a scenario into a fresh mailbox. Returns (mailbox_id, package_id)."""
    # provider must satisfy mailbox_provider_check (gmail|msgraph); this is a
    # throwaway synthetic mailbox, so the value is cosmetic.
    mbx = orm.Mailbox(provider="gmail", owner_email=data["owner_email"],
                      embed_model="deferred", embed_dim=0, config={})
    session.add(mbx)
    session.flush()
    mid = str(mbx.id)

    owner = orm.Person(mailbox_id=mid, canonical_email=data["owner_email"], names=["Owner"])
    session.add(owner)
    session.flush()
    mbx.owner_person_id = owner.id

    proj_id: dict[str, str] = {}
    for p in data.get("projects", []):
        pr = orm.Project(mailbox_id=mid, label=p["label"], label_source="ctfidf",
                         start=_TS, end=_TS, confidence=0.9)
        session.add(pr)
        session.flush()
        proj_id[p["key"]] = str(pr.id)

    for t in data.get("threads", []):
        tid = str(uuid.uuid4())
        session.add(orm.Thread(id=tid, mailbox_id=mid, subject_norm=t["subject"],
                               t_start=_TS, t_end=_TS))
        session.flush()
        for m in t.get("messages", []):
            display = m.get("display", "")
            session.add(orm.Message(
                mailbox_id=mid, message_id_header=m["header"], provider_id=m["header"],
                thread_id=tid, sender_email=m["sender"], ts=_TS, subject=t["subject"],
                clean_text=m["body"], sensitivity=m.get("sensitivity", ["none"]),
                noise=m.get("noise", False),
                addresses={"sender": {"display_names": [display]}} if display else {},
            ))

    for e in data.get("events", []):
        pk = e.get("project")
        session.add(orm.Event(
            mailbox_id=mid, actor_person_id=owner.id, type=e["type"], summary=e["summary"],
            project_id=proj_id.get(pk) if pk else None,
            source_message_ids=e["headers"], confidence=0.9,
        ))

    sc = data.get("scope", {})
    included = [proj_id[k] for k in sc.get("included_projects", []) if k in proj_id]
    pkg = orm.HandoffPackage(mailbox_id=mid, creator_email=data["owner_email"],
                             reason="vacation", status="draft", lineage_id=str(uuid.uuid4()))
    session.add(pkg)
    session.flush()
    session.add(orm.HandoffScope(
        package_id=pkg.id, date_from=_parse_date(sc.get("date_from")),
        date_to=_parse_date(sc.get("date_to")), included_project_ids=included,
    ))
    session.commit()
    return mid, str(pkg.id)


def collect(session, package_id: str) -> dict:
    """Read back the generated snapshot rows as plain data (detached from the DB)."""
    claims = session.execute(select(orm.HandoffClaim).where(
        orm.HandoffClaim.package_id == package_id)).scalars().all()
    evidence = session.execute(select(orm.HandoffEvidence).where(
        orm.HandoffEvidence.package_id == package_id)).scalars().all()
    exclusions = session.execute(select(orm.HandoffExclusion).where(
        orm.HandoffExclusion.package_id == package_id)).scalars().all()
    return {
        "claims": [{"id": c.id, "kind": c.kind, "text": c.text,
                    "project_label": c.project_label, "confidence": float(c.confidence),
                    "cites": list(c.source_message_id_headers)} for c in claims],
        "evidence": [{"header": e.message_id_header, "sender_domain": e.sender_domain,
                      "sender_display": e.sender_display, "subject": e.subject,
                      "body": e.body_snapshot} for e in evidence],
        "exclusions": [{"type": e.exclusion_type, "target": e.target_ref} for e in exclusions],
    }


def cleanup(session, mailbox_id: str, package_id: str | None) -> None:
    """Delete the throwaway mailbox (cascades messages/threads/events/projects/
    package/claims/evidence). handoff_audit_event has no cascade, so purge it first."""
    session.rollback()
    if package_id:
        session.execute(orm.HandoffAuditEvent.__table__.delete().where(
            orm.HandoffAuditEvent.package_id == package_id))
    session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mailbox_id))
    session.commit()


# -- evaluation (pure) ---------------------------------------------------------

@dataclass
class ScenarioResult:
    name: str
    hard_gates: dict[str, bool]
    quality: dict[str, Any]
    limitations: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def hard_pass(self) -> bool:
        return all(self.hard_gates.values())


def _match(items: list[dict], claims: list[dict], require_kind: str | None):
    """Return (found_labels, missing_labels). An item matches a claim when its
    lowercased ``contains`` is in the claim text AND all its ``cites`` are present."""
    found, missing = [], []
    for g in items:
        needle = g["contains"].lower()
        cites = set(g.get("cites", []))
        hit = any(
            (require_kind is None or c["kind"] == require_kind)
            and needle in c["text"].lower()
            and cites <= set(c["cites"])
            for c in claims
        )
        (found if hit else missing).append(g["contains"])
    return found, missing


def evaluate(data: dict, generated: dict) -> ScenarioResult:
    """Score a generated package against the scenario's gold labels. Pure/DB-free."""
    claims = generated["claims"]
    evidence = generated["evidence"]
    gold = data.get("gold", {})

    ev_headers = {e["header"] for e in evidence}
    all_cites = {h for c in claims for h in c["cites"]}
    excluded = set(gold.get("excluded_headers", []))

    # S48 coverage contract, assembled from the SAME frozen rows (pure, DB-free).
    contract = build_coverage_contract(
        [{"id": c.get("id", ""), "kind": c["kind"], "text": c["text"],
          "project_label": c.get("project_label"), "cites": c["cites"]} for c in claims],
        [e["header"] for e in evidence],
    )
    contract_items = [
        it for entry in contract
        for key in ("decisions", "open_loops", "blockers", "people")
        for it in entry[key]
    ]
    contract_ref_union = {h for entry in contract for h in entry["evidence_refs"]}
    contract_item_headers = {h for it in contract_items for h in it["source_message_id_headers"]}

    hard_gates = {
        "every_claim_cited": all(len(c["cites"]) >= 1 for c in claims),
        "citations_in_evidence": all(h in ev_headers for c in claims for h in c["cites"]),
        "excluded_material_absent": not (excluded & (ev_headers | all_cites)),
        # Every contract item is citation-backed by >= 1 in-package evidence header.
        "contract_items_cited": all(
            it["source_message_id_headers"]
            and all(h in ev_headers for h in it["source_message_id_headers"])
            for it in contract_items
        ),
        # The contract's evidence set equals the package evidence set: nothing
        # invented, nothing silently dropped (the other-evidence bucket reconciles).
        "contract_evidence_reconciles": contract_ref_union == ev_headers,
        # No excluded fixture material appears anywhere in the contract.
        "contract_excluded_absent": not (excluded & (contract_ref_union | contract_item_headers)),
    }

    dec_found, dec_missing = _match(gold.get("decisions", []), claims, "decision")
    ol_found, ol_missing = _match(gold.get("open_loops", []), claims, "open_loop")
    # "blocker content" is kind-agnostic: the generator has no 'blocker' kind, so
    # blocker-shaped work surfaces as an open_loop/decision. "blocker_kind_present"
    # is whether a TRUE blocker-kind claim exists (currently always False - see the
    # limitation below and the S43 plan).
    bl_found, bl_missing = _match(gold.get("blockers", []), claims, None)
    blocker_kind_present = any(c["kind"] == "blocker" for c in claims)

    labels_present = {c["project_label"] for c in claims if c["project_label"]}
    labels_missing = [x for x in gold.get("project_labels", []) if x not in labels_present]

    ev_domains = {e["sender_domain"] for e in evidence}
    stake_missing = [s for s in gold.get("stakeholders", []) if s not in ev_domains]

    gold_needles = [
        (g["contains"].lower(), set(g.get("cites", [])))
        for key in ("decisions", "open_loops", "blockers")
        for g in gold.get(key, [])
    ]
    matched, unexpected = 0, []
    for c in claims:
        if any(n in c["text"].lower() and cs <= set(c["cites"]) for n, cs in gold_needles):
            matched += 1
        else:
            unexpected.append(c["text"])
    precision = round(matched / len(claims), 3) if claims else 0.0

    quality = {
        "decisions_found": len(dec_found), "decisions_expected": len(gold.get("decisions", [])),
        "missing_decisions": dec_missing,
        "open_loops_found": len(ol_found), "open_loops_expected": len(gold.get("open_loops", [])),
        "missing_open_loops": ol_missing,
        "blocker_content_found": len(bl_found),
        "blocker_content_expected": len(gold.get("blockers", [])),
        "missing_blocker_content": bl_missing,
        "blocker_kind_present": blocker_kind_present,
        "project_labels_present": labels_missing == [], "missing_project_labels": labels_missing,
        "stakeholders_present": stake_missing == [], "missing_stakeholders": stake_missing,
        "claim_precision_proxy": precision, "unexpected_claims": unexpected,
    }

    limitations: list[str] = []
    if gold.get("blockers"):
        limitations.append(
            "blocker-kind extraction not implemented: blocker content surfaces as an "
            "open_loop/decision, never labeled a 'blocker' (candidate S44 work)."
        )
    if gold.get("stale_conflict"):
        limitations.append(
            "stale/conflict detection not implemented: contradictory or outdated claims "
            "are surfaced without a flag (candidate S44 work)."
        )

    # S44: deterministic privacy/safety findings over the same snapshot. Compared as
    # (category, severity) SETS so multiple findings of one category collapse.
    findings = scan_package(claims, evidence)
    finding_pairs = sorted({(f.category, f.severity) for f in findings})
    gold_pairs = sorted({(g["category"], g["severity"]) for g in gold.get("expected_findings", [])})
    quality["findings"] = [{"category": c, "severity": s} for c, s in finding_pairs]
    quality["expected_findings_match"] = finding_pairs == gold_pairs
    quality["unexpected_findings"] = [
        {"category": c, "severity": s} for c, s in finding_pairs if (c, s) not in gold_pairs
    ]
    quality["missing_findings"] = [
        {"category": c, "severity": s} for c, s in gold_pairs if (c, s) not in finding_pairs
    ]
    quality["high_severity_finding_present"] = len(high_severity(findings)) > 0

    # S48: coverage-contract shape (informational; the hard gates above enforce it).
    quality["contract_entries"] = len(contract)
    quality["contract_items"] = len(contract_items)

    counts = {"claims": len(claims), "evidence": len(evidence),
              "exclusions": len(generated["exclusions"])}
    return ScenarioResult(name=data["name"], hard_gates=hard_gates, quality=quality,
                          limitations=limitations, counts=counts)


def run_scenario(session, data: dict) -> ScenarioResult:
    """Seed -> generate -> collect -> teardown -> evaluate. Self-cleaning."""
    mid = pkg_id = None
    generated = None
    try:
        mid, pkg_id = seed_scenario(session, data)
        pkg = session.get(orm.HandoffPackage, pkg_id)
        generate_candidate(session, pkg)
        session.commit()
        generated = collect(session, pkg_id)
    finally:
        if mid:
            cleanup(session, mid, pkg_id)
    return evaluate(data, generated)
