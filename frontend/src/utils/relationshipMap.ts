import type {
  ProjectSummary,
  RelationshipEdge,
  RelationshipNodeType,
  RelationshipType,
} from "../api/types";
import { cleanProjectLabel } from "./projectLabels";

/**
 * Visual-encoding helpers for the relationship map (S13). Color encodes node
 * *type* (not arbitrary decoration); edge dash style encodes relationship type;
 * width/opacity encode evidence volume and weak/stale — never importance.
 */

export const NODE_COLOR: Record<RelationshipNodeType, string> = {
  owner: "#0f172a",
  person: "#2563eb",
  project: "#9333ea",
  organization: "#0d9488",
  thread_group: "#64748b",
};

export const REL_TYPE_LABEL: Record<RelationshipType, string> = {
  direct_exchange: "Direct email exchange",
  thread_copresence: "Appeared together in threads",
  project_copresence: "Shared project",
  org_affiliation: "Same organization",
  bridge: "Bridge across projects",
};

/** Dash pattern by relationship type: solid = direct, dashed = co-participation,
 *  dotted = org/domain affiliation. Returns [] for solid. */
export function edgeDash(t: RelationshipType): number[] {
  switch (t) {
    case "direct_exchange":
      return []; // solid
    case "org_affiliation":
      return [1, 3]; // dotted
    case "bridge":
      return [6, 3]; // long dash
    default:
      return [4, 3]; // dashed (co-participation)
  }
}

/** Link width from evidence count, clamped. Tooltip/copy must clarify this is
 *  evidence/volume, not importance. */
export function edgeWidth(e: RelationshipEdge): number {
  const n = Math.max(1, e.evidence_count);
  return Math.min(5, 0.8 + Math.log2(n + 1));
}

export function edgeColor(e: RelationshipEdge): string {
  // Muted (weak or stale) edges are drawn faint; otherwise a neutral slate.
  return e.muted ? "#cbd5e1" : "#94a3b8";
}

/** Human-readable one-liner for an edge, evidence-forward and volume-honest. */
export function edgeSummary(e: RelationshipEdge): string {
  if (e.explanation) return e.explanation;
  return REL_TYPE_LABEL[e.relationship_type];
}

// ── Default project-root selection (S13 demo UX) ──────────────────────────────

// Labels that read like a mailer/automated sender or a bare domain make a poor
// "project" root for a relationship demo (e.g. "Account Https", "ssa.gov").
const LOW_VALUE_LABEL =
  /(no[-_ ]?reply|do[-_ ]?not[-_ ]?reply|donotreply|mailer|postmaster|notification|notifications|automated|account\s+https?)/i;

/** True when a project label is too machine-ish / automated-sender-ish to be a
 *  good default root (still selectable by the user, just not auto-chosen). */
export function isLowValueProjectLabel(label: string): boolean {
  if (LOW_VALUE_LABEL.test(label)) return true;
  // Bare-domain-looking labels (e.g. "ssa.gov", "mail.acme.com").
  if (/^[a-z0-9.-]+\.[a-z]{2,}$/i.test(label.trim())) return true;
  return false;
}

// Caps so one large/noisy cluster (e.g. a 17-thread automated digest) cannot
// dominate the ranking on raw volume.
const MEMBER_CAP = 8;
const THREAD_CAP = 10;

/** Recency bucket from project.end: UTC year*12 + month. Coarse (monthly) so
 *  projects in the same window are then ordered by relationship richness, which
 *  is the "recent AND rich" product goal. Deterministic — derived from the data's
 *  own end timestamp, never wall-clock. Missing/invalid end sorts oldest. */
function _recencyBucket(end: string | null | undefined): number {
  if (!end) return Number.NEGATIVE_INFINITY;
  const d = new Date(end);
  const t = d.getTime();
  if (Number.isNaN(t)) return Number.NEGATIVE_INFINITY;
  return d.getUTCFullYear() * 12 + d.getUTCMonth();
}

/**
 * "Coverage usefulness" ranking for the Project-tree default root. For fast
 * handoff we want a project that is recent AND relationship-rich AND has a real
 * label — not merely the highest-confidence one (often a low-value
 * automated-sender cluster like "Account Https").
 *
 * Ranking tuple, best first:
 *   1. low-value labels last (unknown/uncategorized, no-reply/mailer/automated,
 *      bare-domain, "Account Https" style) — still selectable, just not default;
 *   2. more recent (monthly bucket of project.end) first;
 *   3. more members (capped);
 *   4. more threads (capped);
 *   5. higher confidence (tie-breaker only);
 *   6. label (final deterministic tie-break).
 *
 * Because low-value is the first key, a recent automated/no-reply cluster never
 * beats a slightly older real, relationship-rich project.
 */
function _rootRank(
  p: ProjectSummary,
): [number, number, number, number, number, string] {
  const { uncategorized } = cleanProjectLabel(p.label, p.confidence);
  const lowValue = uncategorized || isLowValueProjectLabel(p.label);
  return [
    lowValue ? 1 : 0,
    -_recencyBucket(p.end),
    -Math.min(p.member_count ?? 0, MEMBER_CAP),
    -Math.min(p.thread_count ?? 0, THREAD_CAP),
    -(p.confidence ?? 0),
    p.label,
  ];
}

/** Projects ordered best-first for use as a relationship-tree root. */
export function sortedProjectRoots(projects: ProjectSummary[]): ProjectSummary[] {
  return [...projects].sort((a, b) => {
    const ra = _rootRank(a);
    const rb = _rootRank(b);
    for (let i = 0; i < ra.length; i++) {
      if (ra[i] < rb[i]) return -1;
      if (ra[i] > rb[i]) return 1;
    }
    return 0;
  });
}

export function pickDefaultProjectRoot(projects: ProjectSummary[]): string | null {
  const sorted = sortedProjectRoots(projects);
  return sorted.length > 0 ? sorted[0].id : null;
}
