import { useState } from "react";
import { useCoverForMe } from "../hooks/useCoverForMe";
import type { EvidenceMessage, RetrievalStatus } from "../api/types";

interface CoverForMeProps {
  mailboxId: string;
}

function CitationChips({
  ids,
  evidence,
}: {
  ids: string[];
  evidence: EvidenceMessage[];
}) {
  const byHeader = Object.fromEntries(evidence.map((e) => [e.message_id_header, e]));
  return (
    <span className="activity-citations">
      {ids.map((id) => {
        const ev = byHeader[id];
        const label = ev
          ? `${ev.subject} · ${new Date(ev.date).toLocaleDateString()}`
          : id;
        return (
          <span key={id} className="citation-chip" title={ev?.snippet ?? id}>
            {label}
          </span>
        );
      })}
    </span>
  );
}

function RetrievalStatusNote({
  status,
  evidenceCount,
}: {
  status: RetrievalStatus;
  evidenceCount: number;
}) {
  switch (status) {
    case "active":
      return evidenceCount > 0 ? (
        <p className="mt-2 text-xs text-slate-400">
          Retrieved {evidenceCount} supporting message
          {evidenceCount !== 1 ? "s" : ""}.
        </p>
      ) : null;
    case "active_l1_only":
      return (
        <p className="mt-2 text-xs text-slate-400">
          No matching retrieved messages — answer uses structured data only.
        </p>
      );
    case "disabled_no_key":
      return (
        <p className="mt-2 text-xs text-slate-400">
          Evidence search not configured — set VOYAGE_API_KEY to enable message
          retrieval.
        </p>
      );
    case "degraded_rate_limit":
      return (
        <p className="mt-2 text-xs text-amber-600">
          Evidence search temporarily limited (rate limit) — answer based on
          structured data only.
        </p>
      );
    case "no_embeddings":
      return (
        <p className="mt-2 text-xs text-amber-600">
          Message embeddings not found — run <code>embed_backfill.py</code> to
          enable retrieval.
        </p>
      );
    case "unavailable":
      return (
        <p className="mt-2 text-xs text-amber-600">
          Evidence search unavailable — answer based on structured data only.
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
export function CoverForMe({ mailboxId }: CoverForMeProps) {
  const [query, setQuery] = useState("");
  const { response, loading, error, notConfigured, ask } =
    useCoverForMe(mailboxId);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    void ask(query);
  };

  const result = response?.result ?? null;
  const claims = result?.claims ?? [];
  const evidence = response?.supporting_evidence ?? [];
  const retrieval_status = response?.retrieval_status ?? "active";
  const isInsufficient =
    !!result &&
    claims.length === 0 &&
    (result.state ?? "").toLowerCase().includes("insufficient");

  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-8">
      <h2 className="text-lg font-semibold text-slate-800">Cover for me</h2>
      <p className="mt-1 text-sm text-slate-500">
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
          className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-400 focus:outline-none"
        />
        <button
          type="submit"
          disabled={loading || !query.trim()}
          className="rounded-md bg-slate-800 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {loading ? "Asking…" : "Ask"}
        </button>
      </form>

      {/* Config error (503) — distinct from a generic failure. */}
      {notConfigured ? (
        <div className="summary-error mt-4" role="alert">
          Summaries are not configured.
        </div>
      ) : null}

      {/* Generic transport/server error — show the API detail so the operator
          can diagnose auth/rate-limit/provider failures without a stack trace. */}
      {error ? (
        <div className="summary-error mt-4" role="alert">
          {error}
        </div>
      ) : null}

      {/* Result. */}
      {result && !loading ? (
        <div className="mt-4">
          {response?.routed_to ? (
            <div className="mb-2 text-xs uppercase tracking-wide text-slate-400">
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
            <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
              No email evidence found for this query. Try asking about a
              specific project or contact name from the mailbox.
            </div>
          ) : claims.length === 0 ? (
            <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-500">
              {result.state || "No evidenced answer in email."}
            </div>
          ) : (
            <div className="summary-result">
              <ul role="list">
                {claims.map((c, i) => (
                  <li key={i} className="summary-claim">
                    <span className="claim-text">{c.text}</span>
                    <CitationChips ids={c.source_message_ids} evidence={evidence} />
                  </li>
                ))}
              </ul>
            </div>
          )}

          <RetrievalStatusNote status={retrieval_status} evidenceCount={evidence.length} />
        </div>
      ) : null}
    </div>
  );
}
