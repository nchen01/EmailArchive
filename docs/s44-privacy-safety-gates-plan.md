# S44 - Privacy/Safety Review Gates

Status: implemented.

## Purpose

Make package review safer before publish. A deterministic, offline scan of a
generated package's OWN snapshot surfaces privacy/safety risks and quality
uncertainty to the creator, and HIGH-severity findings block publish until the
creator removes the flagged content or acknowledges it with an audited reason. This
is the second "quality-first" roadmap item after the S43 eval harness
(`docs/product-roadmap-quality-first.md`), and it uses the two S43 limitations
(blocker-kind, stale/conflict) as finding inputs.

## Boundary (unchanged invariants)

- Findings are **creator-side only** - computed for the creator DTO and the publish
  gate. The recipient payload and admin DTOs are unmodified.
- No recipient live mailbox access; no LLM/Anthropic/Voyage/Gmail/network; no
  integrations.
- Findings carry **safe metadata only** (category, severity, a fixed explanation, a
  package-local claim id / message_id_header) and **never** the matched sensitive
  text.
- The sensitivity/noise/exclusion gates are untouched. The safety scan runs over
  content that already passed those gates, so it is a second, content-pattern layer
  that catches risks the coarse thread/message gates miss (e.g. an API key pasted
  into an otherwise-normal thread). It never re-introduces excluded content.

## Design decisions (proposed and adopted)

1. **Migration?** No. Findings are a pure function of the already-persisted claims +
   evidence, so they are computed on read and always reflect the CURRENT package
   (pruning + regenerate makes a finding disappear). The override is per-publish and
   audited via the existing `handoff_audit_event` table. Persisting findings would
   drift from content and need a migration for no benefit. No `ekc_schemas` change
   (the API DTO additions live in `services/api/schemas/handoff.py`).
2. **Storage shape.** Computed, not stored. `services/handoff/safety.py::scan_package`
   returns `Finding(id, category, severity, explanation, claim_id?, evidence_header?)`.
   The creator DTO gains a `findings` list; the publish request gains an optional
   `safety_ack {reason, acknowledged_finding_ids}`.
3. **Severity model.** `high` (blocks publish), `medium` (warns), `low` (info).
   high: credential_or_secret, payment_financial (Luhn/IBAN), personal_sensitive
   (SSN pattern). medium: hr_legal, security_sensitive, personal_sensitive
   (medical / personal email domain), stale_or_conflicting, blocker_or_dependency.
   low: low_confidence_or_needs_confirmation.
4. **What blocks publish.** Only `high`. medium/low warn but never block.
5. **Override.** Allowed in S44 and audited. Heuristics have false positives, so a
   hard block with no escape would make Publish unusable; the creator may publish
   past high findings by acknowledging every current high finding id with a
   non-blank reason (`SafetyAck.reason`, 1-500 chars). This writes
   `package_published_with_safety_override` with SAFE metadata only - the high count,
   the category names, and that a reason was provided plus its length
   (`reason_provided` / `reason_length`). The raw reason text is untrusted free input
   (it could contain a pasted secret / DB URL), so it is **never** stored in the
   audit - the key-based sanitizer would not catch a `reason` key. No ack / blank
   reason / ack that doesn't cover all current high ids -> 422; an over-length reason
   -> 422.
6. **Resolution.** (a) Remove the flagged claim/evidence and regenerate - the finding
   recomputes away. (b) Per-publish audited override for high findings.
7. **Creator UI.** A compact "Safety review" panel (severity badges + one line per
   finding with a safe category/explanation and the affected claim/evidence ref),
   shown only when findings exist. The Publish control shows a high-severity block
   notice and opens a typed-reason override panel that lists the high findings; it
   passes `safety_ack` on confirm. Zero findings -> nothing shown.
8. **Tests.** DB-free detector tests (each category/severity, no sensitive text in
   explanations, Luhn payment, personal domain, determinism); DB-gated
   (creator DTO surfaces findings, publish blocked 422, audited override publishes,
   removing evidence + regenerate resolves, recipient payload has no findings); and
   the S43 harness high-risk scenario.
9. **S43 harness.** Extended to compute findings per scenario and compare to gold
   `expected_findings`, with a `high_severity_finding_present` metric and a report
   line. New scenario `06_high_risk_content.json` (leaked cloud key + HR topic)
   proves high-risk content is detected and would block publish; the benign
   scenarios produce no high findings.

## Known limitations (candidate future work)

- Detectors are deterministic pattern/rule checks (curated, high-precision). They
  catch pasted credential/payment/ID patterns and topic keywords, not obfuscated or
  novel secrets, and stale/conflict is marker-based (e.g. "switch from X to Y",
  "stale") rather than a semantic contradiction detector. Blocker detection flags
  blocker-worded claims but does not add a true `blocker` claim kind.
- Findings gate the creator publish flow only; there is no separate reviewer/approver
  role gate in S44.
