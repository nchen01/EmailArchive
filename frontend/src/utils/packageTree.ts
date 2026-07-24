import type { RecipientClaim, RecipientEvidence } from "../api/types";

/**
 * Package-local navigation model (S17.12).
 *
 * Derives a lightweight contents outline for the recipient package view from
 * ONLY the already-snapshotted package payload (`RecipientPackage.claims` +
 * `.evidence`). It is intentionally NOT a relationship map: there is no query
 * against Message/Thread/Project/Person/Edge/Event, no relationship-map or
 * source-message API, and no live-mailbox data of any kind — this is a pure
 * function over the two arrays the recipient endpoint already returned.
 *
 * Anchor ids here match the DOM ids that `RecipientPackage` stamps on its claim
 * groups and evidence cards, so a tree click can scroll to the right spot.
 */

export const KIND_LABEL: Record<string, string> = {
  briefing: "Briefing",
  project_state: "Project state",
  open_loop: "Open loops",
  decision: "Decisions",
  blocker: "Blockers",
  person_note: "People notes",
};
export const KIND_ORDER = [
  "briefing",
  "project_state",
  "open_loop",
  "decision",
  "blocker",
  "person_note",
];

/** DOM id helpers — shared by the tree and the document so anchors line up. */
export const kindAnchorId = (kind: string): string => `handoff-kind-${kind}`;
export const evidenceAnchorId = (index: number): string => `handoff-ev-${index}`;
export const CLAIMS_ANCHOR = "handoff-claims";
export const EVIDENCE_ANCHOR = "handoff-evidence";

export interface TreeLeaf {
  /** Stable React key. */
  id: string;
  /** User-facing label — never a raw Message-ID. */
  label: string;
  count: number;
  /** DOM id the click scrolls to. */
  anchorId: string;
}

export interface PackageTree {
  /** Claim kinds present, in KIND_ORDER then any unknown kinds (never dropped). */
  claimGroups: TreeLeaf[];
  /** Sender groups (by domain, else display name), first-appearance order. */
  domainGroups: TreeLeaf[];
  claimCount: number;
  evidenceCount: number;
}

/** Group key + human label for one evidence sender (domain preferred). */
function senderGroup(ev: RecipientEvidence): { key: string; label: string } {
  const domain = (ev.sender_domain || "").trim();
  if (domain) return { key: `d:${domain.toLowerCase()}`, label: domain };
  const display = (ev.sender_display || "").trim();
  if (display) return { key: `n:${display.toLowerCase()}`, label: display };
  return { key: "unknown", label: "Unknown sender" };
}

export function buildPackageTree(
  claims: RecipientClaim[],
  evidence: RecipientEvidence[],
): PackageTree {
  // Claim kinds: known order first, then unknown kinds (in first-seen order) so
  // nothing is ever dropped from the outline.
  const counts = new Map<string, number>();
  const seenOrder: string[] = [];
  for (const c of claims) {
    if (!counts.has(c.kind)) seenOrder.push(c.kind);
    counts.set(c.kind, (counts.get(c.kind) ?? 0) + 1);
  }
  const orderedKinds = [
    ...KIND_ORDER.filter((k) => counts.has(k)),
    ...seenOrder.filter((k) => !KIND_ORDER.includes(k)),
  ];
  const claimGroups: TreeLeaf[] = orderedKinds.map((kind) => ({
    id: `kind-${kind}`,
    label: KIND_LABEL[kind] ?? kind,
    count: counts.get(kind) ?? 0,
    anchorId: kindAnchorId(kind),
  }));

  // Sender groups: first evidence index for each group is the scroll target.
  const groups = new Map<string, { label: string; count: number; firstIndex: number }>();
  evidence.forEach((ev, index) => {
    const { key, label } = senderGroup(ev);
    const existing = groups.get(key);
    if (existing) existing.count += 1;
    else groups.set(key, { label, count: 1, firstIndex: index });
  });
  const domainGroups: TreeLeaf[] = [...groups.entries()].map(([key, g]) => ({
    id: `sender-${key}`,
    label: g.label,
    count: g.count,
    anchorId: evidenceAnchorId(g.firstIndex),
  }));

  return {
    claimGroups,
    domainGroups,
    claimCount: claims.length,
    evidenceCount: evidence.length,
  };
}
