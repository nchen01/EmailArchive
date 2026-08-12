import type { HandoffClaim, HandoffEvidence } from "../api/types";

/**
 * Creator-side Handoff review grouping (S37 fix).
 *
 * Groups a creator package by REAL project identity — `claim.project_id` resolved
 * to a project label from the creator's OWN mailbox project list — instead of the
 * recipient surface's text clustering (utils/coverageAreas), which is built for a
 * snapshot payload that carries no project labels and can transitively merge every
 * claim into one giant area. This is allowed here because the creator owns the
 * mailbox; it introduces NO recipient live-mailbox access and no snapshot change.
 *
 * Grouping rules:
 *  - Claims bucket by `project_id`; a group's evidence is the distinct in-package
 *    messages its claims cite (package order). Evidence cited by claims in more
 *    than one project appears in EACH relevant group (never dropped).
 *  - `project_id === null` → an honest "Unassigned / cross-project" fallback.
 *  - A `project_id` with no matching label → "Untitled project · <id-frag>"
 *    (honest, id-derived — never a fabricated project name).
 *  - Evidence cited by no surviving claim → an "Other evidence" group.
 */

export const UNASSIGNED_GROUP_ID = "__unassigned__";
export const OTHER_EVIDENCE_GROUP_ID = "__other_evidence__";

export interface HandoffGroup {
  /** project_id, or one of the reserved fallback ids above. */
  id: string;
  /** Real project label, or an honest fallback (never a fabricated name). */
  label: string;
  /** True for unassigned / unlabeled / other-evidence — not a named project. */
  isFallback: boolean;
  claims: HandoffClaim[];
  evidence: HandoffEvidence[];
}

function evHaystack(e: HandoffEvidence): string {
  return `${e.subject} ${e.sender_display} ${e.sender_domain} ${e.body_snapshot}`.toLowerCase();
}

export function buildHandoffProjectGroups(
  claims: HandoffClaim[],
  evidence: HandoffEvidence[],
  labelById: Map<string, string>,
): HandoffGroup[] {
  const byHeader = new Map<string, HandoffEvidence>();
  const order = new Map<string, number>();
  evidence.forEach((e, i) => {
    byHeader.set(e.message_id_header, e);
    order.set(e.message_id_header, i);
  });

  const buckets = new Map<string, HandoffClaim[]>();
  const keyOrder: string[] = [];
  for (const c of claims) {
    const key = c.project_id ?? UNASSIGNED_GROUP_ID;
    if (!buckets.has(key)) {
      buckets.set(key, []);
      keyOrder.push(key);
    }
    buckets.get(key)!.push(c);
  }

  const citedHeaders = new Set<string>();
  const groups: HandoffGroup[] = [];
  for (const key of keyOrder) {
    const groupClaims = buckets.get(key)!;
    const headerSet = new Set<string>();
    for (const c of groupClaims) {
      for (const h of c.source_message_id_headers) {
        if (byHeader.has(h)) {
          headerSet.add(h);
          citedHeaders.add(h);
        }
      }
    }
    const groupEvidence = [...headerSet]
      .sort((a, b) => order.get(a)! - order.get(b)!)
      .map((h) => byHeader.get(h)!);

    let label: string;
    let isFallback: boolean;
    if (key === UNASSIGNED_GROUP_ID) {
      label = "Unassigned / cross-project";
      isFallback = true;
    } else {
      const resolved = labelById.get(key);
      if (resolved && resolved.trim()) {
        label = resolved.trim();
        isFallback = false;
      } else {
        // Honest, id-derived placeholder — distinct per project, never invented.
        label = `Untitled project · ${key.slice(0, 6)}`;
        isFallback = true;
      }
    }
    groups.push({ id: key, label, isFallback, claims: groupClaims, evidence: groupEvidence });
  }

  // Named projects A–Z first, then fallback (unassigned / unlabeled) groups.
  groups.sort((a, b) => {
    if (a.isFallback !== b.isFallback) return a.isFallback ? 1 : -1;
    return a.label.localeCompare(b.label);
  });

  const ungrouped = evidence.filter((e) => !citedHeaders.has(e.message_id_header));
  if (ungrouped.length > 0) {
    groups.push({
      id: OTHER_EVIDENCE_GROUP_ID,
      label: "Other evidence (not cited by a claim)",
      isFallback: true,
      claims: [],
      evidence: ungrouped,
    });
  }
  return groups;
}

export interface FilteredGroup {
  claims: HandoffClaim[];
  evidence: HandoffEvidence[];
}

/**
 * Narrow a group's claims + evidence by a lowercased query — WITHIN the group,
 * not all-or-nothing. A group-label match (e.g. "nexus" → "Nexus Auth Platform")
 * shows the whole group; otherwise a claim is kept when its text matches or it
 * cites matching evidence, and evidence is kept when it matches or is cited by a
 * kept claim. Returns the (possibly empty) visible subset.
 */
export function filterHandoffGroup(group: HandoffGroup, q: string): FilteredGroup {
  if (!q) return { claims: group.claims, evidence: group.evidence };
  if (group.label.toLowerCase().includes(q)) {
    return { claims: group.claims, evidence: group.evidence };
  }
  const evMatch = new Set<string>();
  for (const e of group.evidence) {
    if (evHaystack(e).includes(q)) evMatch.add(e.message_id_header);
  }
  const claims = group.claims.filter(
    (c) =>
      c.text.toLowerCase().includes(q) ||
      c.source_message_id_headers.some((h) => evMatch.has(h)),
  );
  const keepHeaders = new Set<string>(evMatch);
  for (const c of claims) for (const h of c.source_message_id_headers) keepHeaders.add(h);
  const evidence = group.evidence.filter((e) => keepHeaders.has(e.message_id_header));
  return { claims, evidence };
}
