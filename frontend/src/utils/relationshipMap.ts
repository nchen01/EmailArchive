import type {
  RelationshipEdge,
  RelationshipNodeType,
  RelationshipType,
} from "../api/types";

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
