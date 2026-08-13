import { useEffect, useState } from "react";
import { useCoverForMe } from "../hooks/useCoverForMe";
import { errorKindTitle } from "../api/client";
import type { EvidenceMessage, RetrievalStatus, SynthesisClaim } from "../api/types";
import type { Suggestion } from "../utils/suggestions";
import { EvidenceDrawer, type SelectedCitation } from "./EvidenceDrawer";

interface CoverForMeProps {
  mailboxId: string;
  /**
   * Optional seeded question (e.g. an Overview suggested-question chip). The
   * `token` lets the same question be re-seeded; the effect re-fires on token
   * change and auto-asks. Null/undefined means no seed.
   */
  seed?: { query: string; token: number } | null;
  /** Suggested starter questions shown in the empty state (S12.5). */
  suggestions?: Suggestion[];
}

// ── Temporal grouping for "next steps" answers (S35 follow-up) ──────────────
//
// The next-steps intent shaping (services/synthesis/intent.py NEXT_STEP_BUCKETS)
// asks the model to prefix each next-step claim with exactly one timing tag —
// "[Now / this week]", "[Upcoming]", "[Later]", or "[No explicit date found]" —
// using dates only when they are explicit in the evidence. We parse that leading
// tag here to render the answer as ordered timing groups instead of a flat
// to-do list, so later work never reads as an immediate task. The claim contract
// is unchanged (the tag lives inside claim.text); grouping is presentation only.
type BucketKey = "now" | "upcoming" | "later" | "none";

const BUCKET_ORDER: { key: BucketKey; label: string }[] = [
  { key: "now", label: "Now / this week" },
  { key: "upcoming", label: "Upcoming" },
  { key: "later", label: "Later" },
  { key: "none", label: "No explicit date found" },
];

/**
 * Parse a leading timing tag off a next-step claim, e.g.
 * "[Now / this week] Rotate the remaining service keys." → { key: "now", text:
 * "Rotate the remaining service keys." }. Returns key=null when the claim has no
 * recognized tag, so non-next-steps answers (blocked/status/summary) render flat.
 */
function parseBucket(text: string): { key: BucketKey | null; text: string } {
  const m = /^\s*\[([^\]]+)\]\s*(.*)$/s.exec(text);
  if (!m) return { key: null, text };
  const tag = m[1].toLowerCase();
  let key: BucketKey;
  if (tag.includes("now") || tag.includes("this week")) key = "now";
  else if (tag.includes("upcoming")) key = "upcoming";
  else if (tag.includes("later")) key = "later";
  else if (tag.includes("no explicit") || tag.includes("no date")) key = "none";
  else return { key: null, text }; // unrecognized bracket — leave text intact
  return { key, text: m[2] };
}

interface ParsedClaim {
  claim: SynthesisClaim;
  text: string; // display text with any timing tag stripped
}

/**
 * Group claims into ordered timing buckets when this is a next-steps answer.
 * Returns null when no claim carries a recognized timing tag, in which case the
 * caller renders the flat list unchanged. A tagged answer whose individual items
 * lack a tag places those items under "No explicit date found" (never invented).
 */
function buildTimingGroups(
  claims: SynthesisClaim[],
): { key: BucketKey; label: string; items: ParsedClaim[] }[] | null {
  const parsed = claims.map((c) => {
    const { key, text } = parseBucket(c.text);
    return { key, claim: c, text };
  });
  if (!parsed.some((p) => p.key !== null)) return null; // not a next-steps answer
  const byKey = new Map<BucketKey, ParsedClaim[]>();
  for (const p of parsed) {
    const k: BucketKey = p.key ?? "none";
    if (!byKey.has(k)) byKey.set(k, []);
    byKey.get(k)!.push({ claim: p.claim, text: p.text });
  }
  return BUCKET_ORDER.filter((b) => byKey.has(b.key)).map((b) => ({
    ...b,
    items: byKey.get(b.key)!,
  }));
}

/** One expandable evidence card: subject · date + snippet, opens the full drawer. */
function EvidenceCard({
  id,
  evidence,
  count,
  onSelect,
}: {
  id: string;
  evidence: EvidenceMessage | undefined;
  count: number;
  onSelect: (c: SelectedCitation) => void;
}) {
  const date =
    evidence?.date ? new Date(evidence.date).toLocaleDateString() : "";
  return (
    <button
      type="button"
      className="evidence-card"
      title="Click to inspect this citation"
      onClick={() => onSelect({ id, evidence, citationCount: count })}
    >
      <span className="evidence-card-head">
        <span className="evidence-card-subject">
          {evidence?.subject || id}
        </span>
        {date ? <span className="evidence-card-date">{date}</span> : null}
      </span>
      {evidence?.snippet ? (
        <span className="evidence-card-snippet">{evidence.snippet}</span>
      ) : (
        <span className="evidence-card-snippet evidence-card-snippet-empty">
          No message preview. Open for citation detail.
        </span>
      )}
    </button>
  );
}

/**
 * One answer item: the claim text, then a compact grounded-source count and a
 * toggle, with the supporting evidence progressively disclosed (S35 follow-up).
 * Evidence is collapsed by default so the answer reads cleanly first; the small
 * "N sources" count stays visible so the answer still looks grounded. No-citation
 * -no-claim and the sensitivity/noise gates are enforced upstream (the backend
 * allow-list), so nothing shown here can leak — it only hides/reveals what the
 * response already contains.
 */
function ClaimItem({
  claim,
  displayText,
  byHeader,
  counts,
  open,
  onToggle,
  onSelect,
}: {
  claim: SynthesisClaim;
  displayText: string;
  byHeader: Record<string, EvidenceMessage>;
  counts: Record<string, number>;
  open: boolean;
  onToggle: () => void;
  onSelect: (c: SelectedCitation) => void;
}) {
  // Collapse repeated citations within a claim to unique headers (first-seen).
  const uniqueIds = [...new Set(claim.source_message_ids)];
  const n = uniqueIds.length;
  return (
    <li className="summary-claim">
      <span className="claim-text">{displayText}</span>
      <div className="claim-evidence-controls">
        <span className="claim-source-count" title="Grounded in cited messages">
          {n} source{n !== 1 ? "s" : ""}
        </span>
        <button
          type="button"
          className="evidence-toggle"
          aria-expanded={open}
          onClick={onToggle}
        >
          {open
            ? "Hide evidence"
            : `Show ${n} supporting message${n !== 1 ? "s" : ""}`}
        </button>
      </div>
      {open ? (
        <div className="claim-evidence-list">
          {uniqueIds.map((id) => (
            <EvidenceCard
              key={id}
              id={id}
              evidence={byHeader[id]}
              count={counts[id] ?? 1}
              onSelect={onSelect}
            />
          ))}
        </div>
      ) : null}
    </li>
  );
}

function RetrievalStatusNote({
  status,
  evidenceCount,
  hasClaims,
}: {
  status: RetrievalStatus;
  evidenceCount: number;
  hasClaims: boolean;
}) {
  // When L1 already produced a cited answer, the L2 (semantic-retrieval)
  // operational state must not read as a failure. `retrieval_status` describes
  // only L2; a degraded L2 state alongside real claims gets a calm, faint note
  // instead of the warn banner so a successful structured answer never looks
  // broken (S35 follow-up C). The warn banner is preserved when there is NO L1
  // answer, so a genuinely empty/failed retrieval still surfaces prominently.
  const l2Degraded =
    status === "no_embeddings" ||
    status === "unavailable" ||
    status === "degraded_rate_limit" ||
    status === "disabled_no_key";
  if (hasClaims && l2Degraded) {
    return (
      <p className="mt-2 text-xs text-faint">
        Answered from structured mailbox evidence. Semantic retrieval is not
        enabled for broader searches.
      </p>
    );
  }
  switch (status) {
    case "active":
      return evidenceCount > 0 ? (
        <p className="mt-2 text-xs text-faint">
          Retrieved {evidenceCount} supporting message
          {evidenceCount !== 1 ? "s" : ""}.
        </p>
      ) : null;
    case "active_l1_only":
      return (
        <p className="mt-2 text-xs text-faint">
          No matching retrieved messages. Answer uses structured data only.
        </p>
      );
    case "disabled_no_key":
      return (
        <p className="mt-2 text-xs text-faint">
          Evidence search not configured. Set VOYAGE_API_KEY to enable message
          retrieval.
        </p>
      );
    case "degraded_rate_limit":
      return (
        <p className="mt-2 text-xs text-warn">
          Evidence search temporarily limited (rate limit). Answer based on
          structured data only.
        </p>
      );
    case "no_embeddings":
      return (
        <p className="mt-2 text-xs text-warn">
          Message embeddings not found. Run <code>embed_backfill.py</code> to
          enable retrieval.
        </p>
      );
    case "unavailable":
      return (
        <p className="mt-2 text-xs text-warn">
          Evidence search unavailable. Answer based on structured data only.
        </p>
      );
    default:
      return null;
  }
}

/**
 * Cover-for-me query surface (S5, D11) — the third MVP surface. A bounded
 * natural-language box that answers "Who do I ask about Y / what's the state of
 * project Z?" over structured L1 data, with every claim cited.
 *
 * The request fires only on the explicit "Ask" button (or Enter) — never on
 * every keystroke. An "insufficient structured evidence" result is rendered as
 * a polite "couldn't find that", distinct from a transport/config error.
 */
export function CoverForMe({ mailboxId, seed, suggestions = [] }: CoverForMeProps) {
  const [query, setQuery] = useState("");
  const [selectedCitation, setSelectedCitation] =
    useState<SelectedCitation | null>(null);
  // Keys of claims whose supporting evidence is currently expanded. Evidence is
  // collapsed by default (S35 follow-up) — this drives per-claim disclosure.
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const { response, loading, error, errorKind, notConfigured, ask, reset } =
    useCoverForMe(mailboxId);

  // Trust boundary: when the mailbox changes, discard the previous mailbox's
  // answer, evidence, and any open citation drawer so mailbox A's evidence can
  // never linger over mailbox B. The query box is cleared too, so a stale
  // question is not silently re-asked against a different mailbox.
  //
  // Manual verification (no frontend test runner is configured in this repo):
  //   1. Open the Cover tab for mailbox A; ask a question; click a citation chip
  //      so the evidence drawer is open.
  //   2. Change the mailbox ID in the header to mailbox B and Load.
  //   3. Expect: the previous answer, citation chips, retrieval note, and the
  //      open evidence drawer all disappear, and the query box is empty.
  //
  // `reset` is stable (useCallback([]) in useCoverForMe), so this effect runs
  // only on mailbox change, not on every render.
  useEffect(() => {
    reset();
    setSelectedCitation(null);
    setQuery("");
  }, [mailboxId, reset]);

  // Seeded question (Overview suggested-question chip). Declared after the
  // mailbox-reset effect so on a fresh mount the seed wins over the clear.
  // Keyed on the token so re-seeding the same text still re-asks.
  useEffect(() => {
    if (!seed || !seed.query.trim()) return;
    setSelectedCitation(null);
    setQuery(seed.query);
    void ask(seed.query);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed?.token]);

  // Every new answer starts with evidence collapsed so it reads cleanly first.
  useEffect(() => {
    setExpanded(new Set());
  }, [response]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setSelectedCitation(null);
    void ask(query);
  };

  const askSuggestion = (q: string) => {
    setSelectedCitation(null);
    setQuery(q);
    void ask(q);
  };

  const result = response?.result ?? null;
  const claims = result?.claims ?? [];
  const evidence = response?.supporting_evidence ?? [];
  const retrieval_status = response?.retrieval_status ?? "active";
  // How many claims cite each header (dedup within a claim first) — powers the
  // drawer's "cited in N places" grouping (S14.4).
  const citationCounts: Record<string, number> = {};
  for (const c of claims) {
    for (const id of new Set(c.source_message_ids)) {
      citationCounts[id] = (citationCounts[id] ?? 0) + 1;
    }
  }
  // header → evidence row, for the progressively-disclosed per-claim cards.
  const byHeader: Record<string, EvidenceMessage> = Object.fromEntries(
    evidence.map((e) => [e.message_id_header, e]),
  );
  // Next-steps answers carry timing tags → render as ordered groups; other
  // answers (blocked/status/summary/changed) have no tags → flat list (null).
  const timingGroups = buildTimingGroups(claims);
  const toggleEvidence = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  // Distinct, human-readable title for a transport/server failure.
  const errorTitle = errorKindTitle(errorKind);
  const isInsufficient =
    !!result &&
    claims.length === 0 &&
    (result.state ?? "").toLowerCase().includes("insufficient");

  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-8">
      <h2 className="text-lg font-semibold text-ink">Cover for me</h2>
      <p className="mt-1 text-sm text-muted">
        Ask who to go to about a project or contact, or for the current state of
        a project. Every answer is grounded in the mailbox and cited.
      </p>

      <form className="mt-4 flex items-center gap-2" onSubmit={submit}>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Ask about a project or contact, e.g. &quot;What's the state of Atlas?&quot;"
          maxLength={500}
          className="flex-1 rounded-md border border-line2 px-3 py-2 text-sm focus:border-line2 focus:outline-none"
        />
        <button
          type="submit"
          disabled={loading || !query.trim()}
          className="rounded-md bg-brass px-4 py-2 text-sm font-medium text-onbrass hover:bg-brass disabled:cursor-not-allowed disabled:bg-brass-soft disabled:text-faint"
        >
          {loading ? "Asking…" : "Ask"}
        </button>
      </form>

      {/* Suggested starter questions — shown before the first query so users who
          don't yet know what to ask have grounded entry points (S12.5). */}
      {!response && !loading && !error && !notConfigured && suggestions.length > 0 ? (
        <div className="mt-4">
          <p className="mb-2 text-xs text-faint">Try one of these:</p>
          <div className="overview-suggestions">
            {suggestions.map((s) => (
              <button
                key={`${s.label}:${s.query}`}
                type="button"
                className="suggestion-chip"
                onClick={() => askSuggestion(s.query)}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {/* Config error (503) — distinct from a generic failure. */}
      {notConfigured ? (
        <div className="summary-error mt-4" role="alert">
          Summaries are not configured.
        </div>
      ) : null}

      {/* Generic transport/server error — distinct, human-readable title plus
          the API detail so the operator can diagnose auth/rate-limit/provider
          failures without a stack trace. */}
      {error ? (
        <div className="summary-error mt-4" role="alert">
          <strong className="block">{errorTitle}</strong>
          <span>{error}</span>
        </div>
      ) : null}

      {/* Result. */}
      {result && !loading ? (
        <div className="mt-4">
          {response?.routed_to ? (
            <div className="mb-2 text-xs uppercase tracking-wide text-faint">
              {response.routed_to.startsWith("project:")
                ? `Project · ${response.routed_to.slice("project:".length)}`
                : response.routed_to.startsWith("person:")
                  ? `Contact · ${response.routed_to.slice("person:".length)}`
                  : response.routed_to}
            </div>
          ) : null}

          {isInsufficient ? (
            // Polite "no evidence found" — NOT an error. Covers both L1 and L2
            // finding nothing for this query.
            <div className="rounded-md border border-line bg-app2 px-4 py-3 text-sm text-muted">
              No email evidence found for this query. Try asking about a
              specific project or contact name from the mailbox.
            </div>
          ) : claims.length === 0 ? (
            <div className="rounded-md border border-line bg-app2 px-4 py-3 text-sm text-muted">
              {result.state || "No evidenced answer in email."}
            </div>
          ) : timingGroups ? (
            // Next-steps answer — grouped by explicit timing so later work does
            // not read as urgent. Each item still discloses its own evidence.
            <div className="summary-result">
              {timingGroups.map((g) => (
                <div key={g.key} className="next-step-group">
                  <h4 className="next-step-group-head">{g.label}</h4>
                  <ul role="list">
                    {g.items.map((it, i) => {
                      const key = `${g.key}-${i}`;
                      return (
                        <ClaimItem
                          key={key}
                          claim={it.claim}
                          displayText={it.text}
                          byHeader={byHeader}
                          counts={citationCounts}
                          open={expanded.has(key)}
                          onToggle={() => toggleEvidence(key)}
                          onSelect={setSelectedCitation}
                        />
                      );
                    })}
                  </ul>
                </div>
              ))}
            </div>
          ) : (
            <div className="summary-result">
              <ul role="list">
                {claims.map((c, i) => {
                  const key = `c-${i}`;
                  return (
                    <ClaimItem
                      key={key}
                      claim={c}
                      displayText={c.text}
                      byHeader={byHeader}
                      counts={citationCounts}
                      open={expanded.has(key)}
                      onToggle={() => toggleEvidence(key)}
                      onSelect={setSelectedCitation}
                    />
                  );
                })}
              </ul>
            </div>
          )}

          <RetrievalStatusNote
            status={retrieval_status}
            evidenceCount={evidence.length}
            hasClaims={claims.length > 0}
          />
        </div>
      ) : null}

      <EvidenceDrawer
        selected={selectedCitation}
        retrievalStatus={retrieval_status}
        onClose={() => setSelectedCitation(null)}
      />
    </div>
  );
}
