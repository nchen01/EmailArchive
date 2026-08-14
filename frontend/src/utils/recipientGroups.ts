import type { RecipientClaim, RecipientEvidence } from "../api/types";
import {
  buildCoverageAreas,
  type CoverageArea,
  peopleForEvidence,
} from "./coverageAreas";

/**
 * Recipient package grouping selector (S39).
 *
 * Groups a recipient package by the FROZEN project/coverage label snapshotted onto
 * each claim at publish time (`claim.project_label`) — real project identity, not
 * the representative-evidence-subject text clustering used before. Everything here
 * is derived PURELY from the package-local snapshot payload (claims + evidence);
 * it makes no API call and never resolves a project id against live rows.
 *
 * Degradation (per the S39 product decision):
 *  - No claim carries a label (pre-S39 package) → the whole package falls back to
 *    the existing `buildCoverageAreas` clustering, unchanged.
 *  - Labeled claims group by their frozen label.
 *  - Unlabeled claims in a MIXED package are NOT dumped into one giant bucket:
 *    they run through `buildCoverageAreas`; if that yields >= 2 useful areas we
 *    keep them, otherwise they collapse into one honest "Other coverage" bucket.
 *
 * The output is `CoverageArea[]` (the exact shape the recipient rail already
 * renders), so this is a drop-in replacement for the previous grouping call.
 */

function hasLabel(c: RecipientClaim): boolean {
  return (c.project_label ?? "").trim() !== "";
}

function areaFrom(
  id: string,
  label: string,
  claims: RecipientClaim[],
  byHeader: Map<string, RecipientEvidence>,
  evOrder: Map<string, number>,
): CoverageArea {
  const headerSet = new Set<string>();
  for (const c of claims) {
    for (const h of c.source_message_id_headers) if (byHeader.has(h)) headerSet.add(h);
  }
  const evidence = [...headerSet]
    .sort((a, b) => (evOrder.get(a) ?? 0) - (evOrder.get(b) ?? 0))
    .map((h) => byHeader.get(h)!);
  return {
    id,
    label,
    claims,
    evidence,
    decisionCount: claims.filter((c) => c.kind === "decision").length,
    openLoopCount: claims.filter((c) => c.kind === "open_loop").length,
    peopleCount: peopleForEvidence(evidence).length,
    evidenceCount: evidence.length,
  };
}

function groupByLabel(
  claims: RecipientClaim[],
  byHeader: Map<string, RecipientEvidence>,
  evOrder: Map<string, number>,
): CoverageArea[] {
  const groups = new Map<string, RecipientClaim[]>();
  const order: string[] = [];
  for (const c of claims) {
    const key = (c.project_label ?? "").trim();
    if (!groups.has(key)) {
      groups.set(key, []);
      order.push(key);
    }
    groups.get(key)!.push(c);
  }
  const areas = order.map((label, idx) =>
    areaFrom(`proj-${idx}`, label, groups.get(label)!, byHeader, evOrder),
  );
  // Recommended-first: most decisions+open-loops, then most claims, then label —
  // the same ordering coverageAreas uses, so the rail feels consistent.
  areas.sort((a, b) => {
    const an = a.decisionCount + a.openLoopCount;
    const bn = b.decisionCount + b.openLoopCount;
    if (bn !== an) return bn - an;
    if (b.claims.length !== a.claims.length) return b.claims.length - a.claims.length;
    return a.label.localeCompare(b.label);
  });
  return areas;
}

export function buildRecipientAreas(
  claims: RecipientClaim[],
  evidence: RecipientEvidence[],
): CoverageArea[] {
  // Pre-S39 / no-label package → unchanged clustering fallback.
  if (!claims.some(hasLabel)) return buildCoverageAreas(claims, evidence);

  const byHeader = new Map<string, RecipientEvidence>();
  const evOrder = new Map<string, number>();
  evidence.forEach((e, i) => {
    byHeader.set(e.message_id_header, e);
    evOrder.set(e.message_id_header, i);
  });

  const labeled = claims.filter(hasLabel);
  const unlabeled = claims.filter((c) => !hasLabel(c));
  const labeledAreas = groupByLabel(labeled, byHeader, evOrder);

  if (unlabeled.length === 0) return labeledAreas;

  // Unlabeled remainder: try honest clustering; keep it only if it yields more
  // than one useful area, else collapse into a single "Other coverage" bucket.
  const clustered = buildCoverageAreas(unlabeled, evidence);
  if (clustered.length >= 2) return [...labeledAreas, ...clustered];
  return [
    ...labeledAreas,
    areaFrom("other-coverage", "Other coverage", unlabeled, byHeader, evOrder),
  ];
}
