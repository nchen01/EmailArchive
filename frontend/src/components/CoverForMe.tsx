import { useState } from "react";
import { useCoverForMe } from "../hooks/useCoverForMe";

interface CoverForMeProps {
  mailboxId: string;
}

function CitationChips({ ids }: { ids: string[] }) {
  return (
    <span className="activity-citations">
      {ids.map((id) => (
        <span key={id} className="citation-chip" title={id}>
          {id}
        </span>
      ))}
    </span>
  );
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

      {/* Generic transport/server error. */}
      {error ? (
        <div className="summary-error mt-4" role="alert">
          Could not answer that. Please try again.
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
            // Polite "couldn't find that in the data" — NOT an error.
            <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-500">
              Couldn't find that in the structured data. Try rephrasing your
              question to mention a specific project or contact name.
            </div>
          ) : claims.length === 0 ? (
            <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-500">
              {result.state ?? "No evidenced answer in email."}
            </div>
          ) : (
            <div className="summary-result">
              <ul role="list">
                {claims.map((c, i) => (
                  <li key={i} className="summary-claim">
                    <span className="claim-text">{c.text}</span>
                    <CitationChips ids={c.source_message_ids} />
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
