import type { RecipientClaim, RecipientEvidence } from "../api/types";

/**
 * Package-local "coverage areas" (S17.17).
 *
 * Groups a recipient package into compact topic sections derived PURELY from the
 * snapshotted payload (`claims` + `evidence`) — no API calls, and no
 * Message/Thread/Project/Person/Edge/Event/source-message/Gmail/retrieval access.
 *
 * The recipient payload does NOT carry true Project labels (claim.project_id is a
 * bare id, often absent), so these are honestly labelled "coverage areas" and
 * their names are a representative *evidence subject* from the group — never a
 * fabricated project name. Grouping is a conservative clustering: two claims join
 * an area if they cite a shared evidence message, OR their claim+evidence text
 * shares >= 2 salient tokens, OR they share a single *distinctive* token (one
 * that occurs in at most 2 of the package's claims — a rare term like "nexus" or
 * "soc2" is a strong topic signal, whereas a generic word occurs broadly and
 * never links). This avoids merging distinct topics that share one common word,
 * at the cost of occasionally splitting a topic (an honest degradation — more,
 * smaller areas, never a false merge).
 */

const _TOKEN_RE = /[a-z0-9]+/g;
const _MIN_TOKEN_LEN = 3;
const _SHARED_TOKEN_THRESHOLD = 2;
// A token in <= this many claims is "distinctive" — a single shared distinctive
// token is enough to link two claims (a rare term is a strong topic signal).
const _DISTINCTIVE_MAX_DF = 2;
const _STOPWORDS = new Set([
  "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "for", "with",
  "at", "by", "from", "as", "is", "are", "was", "were", "be", "been", "it", "its",
  "this", "that", "these", "those", "we", "our", "you", "your", "they", "their",
  "next", "sprint", "week", "weekly", "update", "updates", "status", "items",
  "item", "remaining", "review", "notes", "note", "plan", "plans", "steps", "step",
  "inputs", "input", "fyi", "re", "fwd", "all", "new", "now", "has", "have", "had",
  "will", "into", "out", "off", "per", "via", "not", "no", "yes",
]);

function _terms(text: string): Set<string> {
  const out = new Set<string>();
  for (const m of (text || "").toLowerCase().matchAll(_TOKEN_RE)) {
    const t = m[0];
    if (t.length >= _MIN_TOKEN_LEN && !_STOPWORDS.has(t)) out.add(t);
  }
  return out;
}

export interface CoverageArea {
  id: string;
  /** Honest label: a representative evidence subject from the area (never faked). */
  label: string;
  claims: RecipientClaim[];
  evidence: RecipientEvidence[];
  decisionCount: number;
  openLoopCount: number;
  /** Distinct sender domains/names appearing in this area's evidence. */
  peopleCount: number;
  evidenceCount: number;
}

/** Sender group label for a piece of evidence (domain preferred, else display). */
export function evidenceSenderLabel(ev: RecipientEvidence): string {
  const domain = (ev.sender_domain || "").trim();
  if (domain) return domain;
  const display = (ev.sender_display || "").trim();
  return display || "Unknown sender";
}

/** Distinct people/domain labels across a set of evidence (order-preserving). */
export function peopleForEvidence(evidence: RecipientEvidence[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const e of evidence) {
    const label = evidenceSenderLabel(e);
    const key = label.toLowerCase();
    if (!seen.has(key)) {
      seen.add(key);
      out.push(label);
    }
  }
  return out;
}

export interface AreaPerson {
  /** Display name if the package snapshotted one, else the address local-part. */
  name: string;
  /** Sender email domain (may be empty). */
  domain: string;
  /** HONEST, package-local context only — never an invented role and never a
   * volume/importance signal. */
  context: string;
}

/**
 * People related to a set of evidence, derived ONLY from package-local sender
 * fields. Distinct by (display, domain), first-appearance order (NOT ranked by
 * message volume — that would imply importance). Context is honest: someone who
 * authored a cited message is a "Sender"; a domain-only entry is a "Domain
 * contact". No roles are invented.
 */
export function peopleDetailForEvidence(evidence: RecipientEvidence[]): AreaPerson[] {
  const seen = new Set<string>();
  const out: AreaPerson[] = [];
  for (const e of evidence) {
    const domain = (e.sender_domain || "").trim();
    const display = (e.sender_display || "").trim();
    const name = display || (domain ? "Domain contact" : "Unknown sender");
    const key = `${display.toLowerCase()}|${domain.toLowerCase()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({
      name,
      domain,
      context: display ? "Sender" : "Domain contact",
    });
  }
  return out;
}

// ── Union-Find ───────────────────────────────────────────────────────────────
class DSU {
  private parent: number[];
  constructor(n: number) {
    this.parent = Array.from({ length: n }, (_, i) => i);
  }
  find(x: number): number {
    while (this.parent[x] !== x) {
      this.parent[x] = this.parent[this.parent[x]];
      x = this.parent[x];
    }
    return x;
  }
  union(a: number, b: number): void {
    this.parent[this.find(a)] = this.find(b);
  }
}

export function buildCoverageAreas(
  claims: RecipientClaim[],
  evidence: RecipientEvidence[],
): CoverageArea[] {
  const byHeader = new Map<string, RecipientEvidence>();
  const evOrder = new Map<string, number>();
  evidence.forEach((e, i) => {
    byHeader.set(e.message_id_header, e);
    evOrder.set(e.message_id_header, i);
  });

  // Per-claim salient tokens (claim text + its cited evidence subjects) and its
  // in-package cited headers.
  const tokens: Set<string>[] = [];
  const headers: Set<string>[] = [];
  for (const c of claims) {
    const inPkg = c.source_message_id_headers.filter((h) => byHeader.has(h));
    const subjectText = inPkg.map((h) => byHeader.get(h)!.subject || "").join(" ");
    tokens.push(_terms(`${c.text} ${subjectText}`));
    headers.push(new Set(inPkg));
  }

  // Document frequency: how many claims each salient token appears in. A low df
  // marks a distinctive (topic-defining) term.
  const df = new Map<string, number>();
  for (const ts of tokens) for (const t of ts) df.set(t, (df.get(t) ?? 0) + 1);

  // Cluster: shared cited header OR >= 2 shared tokens OR one shared distinctive
  // (df <= 2) token.
  const dsu = new DSU(claims.length);
  for (let i = 0; i < claims.length; i++) {
    for (let j = i + 1; j < claims.length; j++) {
      let linked = false;
      for (const h of headers[i]) {
        if (headers[j].has(h)) { linked = true; break; }
      }
      if (!linked) {
        let shared = 0;
        for (const t of tokens[i]) {
          if (!tokens[j].has(t)) continue;
          shared += 1;
          if ((df.get(t) ?? 0) <= _DISTINCTIVE_MAX_DF) { linked = true; break; }
          if (shared >= _SHARED_TOKEN_THRESHOLD) { linked = true; break; }
        }
      }
      if (linked) dsu.union(i, j);
    }
  }

  // Assemble areas by component, preserving first-appearance order.
  const groups = new Map<number, number[]>();
  const groupOrder: number[] = [];
  claims.forEach((_c, i) => {
    const root = dsu.find(i);
    if (!groups.has(root)) {
      groups.set(root, []);
      groupOrder.push(root);
    }
    groups.get(root)!.push(i);
  });

  const areas: CoverageArea[] = groupOrder.map((root, idx) => {
    const claimIdxs = groups.get(root)!;
    const areaClaims = claimIdxs.map((i) => claims[i]);

    // Evidence = union of the area's claims' in-package citations, in package order.
    const headerSet = new Set<string>();
    for (const i of claimIdxs) for (const h of headers[i]) headerSet.add(h);
    const areaEvidence = [...headerSet]
      .sort((a, b) => (evOrder.get(a)! - evOrder.get(b)!))
      .map((h) => byHeader.get(h)!);

    // Honest label: the first (earliest package-order) evidence subject; fall
    // back to the first claim text if evidence has no subject.
    const label =
      areaEvidence.find((e) => (e.subject || "").trim())?.subject?.trim() ||
      areaClaims[0]?.text?.slice(0, 60) ||
      "Coverage area";

    return {
      id: `area-${idx}`,
      label,
      claims: areaClaims,
      evidence: areaEvidence,
      decisionCount: areaClaims.filter((c) => c.kind === "decision").length,
      openLoopCount: areaClaims.filter((c) => c.kind === "open_loop").length,
      peopleCount: peopleForEvidence(areaEvidence).length,
      evidenceCount: areaEvidence.length,
    };
  });

  // Recommended-first: most decisions+open-loops, then most claims, then label.
  areas.sort((a, b) => {
    const an = a.decisionCount + a.openLoopCount;
    const bn = b.decisionCount + b.openLoopCount;
    if (bn !== an) return bn - an;
    if (b.claims.length !== a.claims.length) return b.claims.length - a.claims.length;
    return a.label.localeCompare(b.label);
  });
  return areas;
}
