import type {
  RelationshipEdge,
  RelationshipMapResponse,
  RelationshipNode,
} from "../api/types";

/**
 * Frontend-only readability transforms for the relationship map (S36).
 *
 * These helpers group people by their org/domain and let the UI progressively
 * disclose a busy graph: large organizations are collapsed (their members
 * hidden from the canvas) by default, and the user expands them on demand. This
 * is purely a *view* transform over the API response — it never mutates the data
 * model, never re-derives relationships, and never invents edges or nodes.
 *
 * Grouping key is the structural `org_domain` (who shares an email domain), and
 * the only size signal used is a group's MEMBER COUNT — i.e. how many people
 * share a domain. That is not, and must not be presented as, importance,
 * productivity, or a ranking of individuals; it is only how many nodes a group
 * would add to the canvas. Edge `evidence_count` is likewise communication
 * volume, never importance (unchanged from S13).
 */

/** One person in an org group. */
export interface OrgMember {
  id: string;
  label: string;
}

/** A group of people who share an email domain. */
export interface OrgGroup {
  /** Lower-cased org/domain — the grouping key. */
  domain: string;
  /** Display name: a matched organization node's label, else the domain. */
  label: string;
  /** True when a matching organization node is flagged owner-internal. */
  internal: boolean;
  /** People in this group, sorted by label (never by volume). */
  members: OrgMember[];
}

/** A group must have at least this many members to be worth collapsing. */
export const GROUP_MIN_MEMBERS = 2;

/** Default: collapse an (external) org once it has at least this many members. */
export const DEFAULT_COLLAPSE_AT = 5;

function personDomain(n: RelationshipNode): string {
  if (n.node_type !== "person") return "";
  const d = n.metadata?.org_domain;
  return typeof d === "string" ? d.trim().toLowerCase() : "";
}

/**
 * Bucket person nodes by org/domain. Only domains with at least
 * `GROUP_MIN_MEMBERS` people are returned (a lone person is not a "group"). An
 * organization node in the same payload (matched on its `subtitle` domain)
 * supplies a friendlier label and the owner-internal flag. Groups are ordered by
 * member count then label — this only decides panel order, not any ranking of
 * people — and members are ordered alphabetically by label.
 */
export function deriveOrgGroups(nodes: RelationshipNode[]): OrgGroup[] {
  const orgByDomain = new Map<string, { label: string; internal: boolean }>();
  for (const n of nodes) {
    if (n.node_type !== "organization") continue;
    const dom = (n.subtitle ?? "").trim().toLowerCase();
    if (dom) {
      orgByDomain.set(dom, {
        label: n.label || dom,
        internal: n.metadata?.internal === true,
      });
    }
  }

  const byDomain = new Map<string, RelationshipNode[]>();
  for (const n of nodes) {
    const d = personDomain(n);
    if (!d) continue;
    const bucket = byDomain.get(d);
    if (bucket) bucket.push(n);
    else byDomain.set(d, [n]);
  }

  const groups: OrgGroup[] = [];
  for (const [domain, members] of byDomain) {
    if (members.length < GROUP_MIN_MEMBERS) continue;
    const org = orgByDomain.get(domain);
    groups.push({
      domain,
      label: org?.label ?? domain,
      internal: org?.internal ?? false,
      members: members
        .slice()
        .sort((a, b) => a.label.localeCompare(b.label))
        .map((m) => ({ id: m.id, label: m.label })),
    });
  }
  groups.sort(
    (a, b) => b.members.length - a.members.length || a.label.localeCompare(b.label),
  );
  return groups;
}

/**
 * The set of domains collapsed by default. A group is collapsed when it has many
 * members (>= `collapseAt`); the owner-internal org is kept expanded so the
 * user's own people stay visible. This is a decluttering default only — nothing
 * about it ranks people or orgs.
 */
export function defaultCollapsedDomains(
  groups: OrgGroup[],
  collapseAt: number = DEFAULT_COLLAPSE_AT,
): Set<string> {
  const out = new Set<string>();
  for (const g of groups) {
    if (g.internal) continue;
    if (g.members.length >= collapseAt) out.add(g.domain);
  }
  return out;
}

export interface FilteredGraph {
  nodes: RelationshipNode[];
  edges: RelationshipEdge[];
  /** People hidden because their org is collapsed (for the banner copy). */
  hiddenPeople: number;
  /** How many org groups are currently collapsed. */
  collapsedGroups: number;
}

/**
 * Apply the readability filters to the raw map response, returning the nodes and
 * edges that should actually be drawn. Two filters compose:
 *   1. `minEvidence` — drop edges below the evidence-volume threshold (S13).
 *   2. `collapsedDomains` — hide the people belonging to a collapsed org.
 * Any node left with no surviving edge is dropped as an orphan (the root is
 * always kept). Purely a projection: no edge is created, merged, or re-weighted.
 */
export function filterGraph(
  data: RelationshipMapResponse,
  minEvidence: number,
  collapsedDomains: ReadonlySet<string>,
): FilteredGraph {
  const domainOf = new Map<string, string>();
  for (const n of data.nodes) {
    const d = personDomain(n);
    if (d) domainOf.set(n.id, d);
  }
  const isHidden = (id: string): boolean => {
    const d = domainOf.get(id);
    return d !== undefined && collapsedDomains.has(d);
  };

  const edges = data.edges.filter(
    (e) =>
      e.evidence_count >= minEvidence &&
      !isHidden(e.source_id) &&
      !isHidden(e.target_id),
  );

  const referenced = new Set<string>();
  for (const e of edges) {
    referenced.add(e.source_id);
    referenced.add(e.target_id);
  }
  if (data.root) referenced.add(data.root.id);

  const nodes = data.nodes.filter((n) => referenced.has(n.id) && !isHidden(n.id));

  let hiddenPeople = 0;
  for (const id of domainOf.keys()) if (isHidden(id)) hiddenPeople += 1;

  return {
    nodes,
    edges,
    hiddenPeople,
    collapsedGroups: collapsedDomains.size,
  };
}
