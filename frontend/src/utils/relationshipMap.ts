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

/** User-facing sort modes for the Project-root picker. */
export type ProjectRootSortMode =
  | "recommended"
  | "recent"
  | "relationship_rich"
  | "az";

function _lowValue(p: ProjectSummary): boolean {
  const { uncategorized } = cleanProjectLabel(p.label, p.confidence);
  return uncategorized || isLowValueProjectLabel(p.label);
}

function _endTimestamp(end: string | null | undefined): number {
  if (!end) return Number.NEGATIVE_INFINITY;
  const t = new Date(end).getTime();
  return Number.isNaN(t) ? Number.NEGATIVE_INFINITY : t;
}

/**
 * Sort key per mode. Each returns a tuple compared element-wise (ascending), so
 * negate "higher is better" numbers and put low-value first to demote it.
 *
 * - recommended: label quality → recency (monthly bucket) → capped members →
 *   capped threads → confidence → label. The coverage-usefulness default: a
 *   recent automated/no-reply cluster never beats a slightly older real,
 *   relationship-rich project (low-value is the first key).
 * - recent: label quality → project.end desc → label.
 * - relationship_rich: label quality → capped members → capped threads →
 *   confidence → label.
 * - az: PURELY alphabetical by cleaned display label, then raw label — no
 *   low-value demotion, because a user choosing A–Z wants a strict alphabetical
 *   list, not a quality-grouped one.
 */
function _rootKey(p: ProjectSummary, mode: ProjectRootSortMode): (number | string)[] {
  const low = _lowValue(p) ? 1 : 0;
  const members = -Math.min(p.member_count ?? 0, MEMBER_CAP);
  const threads = -Math.min(p.thread_count ?? 0, THREAD_CAP);
  const conf = -(p.confidence ?? 0);
  switch (mode) {
    case "recent":
      return [low, -_endTimestamp(p.end), p.label];
    case "relationship_rich":
      return [low, members, threads, conf, p.label];
    case "az":
      return [cleanProjectLabel(p.label, p.confidence).display.toLowerCase(), p.label.toLowerCase()];
    case "recommended":
    default:
      return [low, -_recencyBucket(p.end), members, threads, conf, p.label];
  }
}

/** Projects ordered best-first for use as a relationship-tree root. */
export function sortedProjectRoots(
  projects: ProjectSummary[],
  mode: ProjectRootSortMode = "recommended",
): ProjectSummary[] {
  return [...projects].sort((a, b) => {
    const ka = _rootKey(a, mode);
    const kb = _rootKey(b, mode);
    for (let i = 0; i < ka.length; i++) {
      if (ka[i] < kb[i]) return -1;
      if (ka[i] > kb[i]) return 1;
    }
    return 0;
  });
}

/** The default seeded root always uses the "recommended" coverage ordering. */
export function pickDefaultProjectRoot(projects: ProjectSummary[]): string | null {
  const sorted = sortedProjectRoots(projects, "recommended");
  return sorted.length > 0 ? sorted[0].id : null;
}
