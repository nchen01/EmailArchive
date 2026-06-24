import type { EvidenceMessage, RetrievalStatus } from "../api/types";

/** The citation the user clicked: the cited header plus its evidence (if any). */
export interface SelectedCitation {
  /** message_id_header from the claim's source_message_ids. */
  id: string;
  /** Matching supporting_evidence row, or undefined when none was returned. */
  evidence: EvidenceMessage | undefined;
}

interface EvidenceDrawerProps {
  selected: SelectedCitation | null;
  /** Answer-level retrieval status, shown as provenance context. */
  retrievalStatus: RetrievalStatus;
  onClose: () => void;
}

const RETRIEVAL_SOURCE_LABEL: Record<RetrievalStatus, string> = {
  active: "Hybrid retrieval (structured + message search)",
  active_l1_only: "Structured data only",
  disabled_no_key: "Structured data only (message search not configured)",
  degraded_rate_limit: "Structured data only (message search rate-limited)",
  no_embeddings: "Structured data only (no message embeddings)",
  unavailable: "Structured data only (message search unavailable)",
};

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/**
 * Slide-in drawer that lets a user inspect *why* a Cover-for-me claim was made
 * by opening the cited source message's metadata: subject, date, the RFC 5322
 * message_id_header (the durable citation key), the normalized snippet, and the
 * retrieval source/status.
 *
 * It only ever renders data already present in `supporting_evidence` — it never
 * fetches raw message bodies, tokens, keys, or provider internals, and sensitive
 * messages never reach `supporting_evidence` (the backend citation allow-list
 * excludes them). When a citation has no matching evidence row it degrades to a
 * clear "detail not available" state rather than inventing content.
 */
export function EvidenceDrawer({
  selected,
  retrievalStatus,
  onClose,
}: EvidenceDrawerProps) {
  const open = selected !== null;
  const ev = selected?.evidence;

  return (
    <aside
      className={`fixed right-0 top-0 z-30 flex h-full w-[380px] flex-col border-l border-slate-200 bg-white shadow-xl transition-transform duration-300 ${
        open ? "translate-x-0" : "translate-x-full"
      }`}
      aria-hidden={!open}
    >
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <h2 className="text-sm font-semibold text-slate-600">Citation detail</h2>
        <button
          type="button"
          onClick={onClose}
          className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
          aria-label="Close citation detail"
        >
          ✕
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {selected ? (
          ev ? (
            <div className="space-y-4">
              <div>
                <div className="text-[11px] uppercase tracking-wide text-slate-400">
                  Subject
                </div>
                <div className="text-sm font-semibold text-slate-900">
                  {ev.subject || "(no subject)"}
                </div>
              </div>

              <div>
                <div className="text-[11px] uppercase tracking-wide text-slate-400">
                  Date
                </div>
                <div className="text-sm text-slate-700">{formatDate(ev.date)}</div>
              </div>

              <div>
                <div className="text-[11px] uppercase tracking-wide text-slate-400">
                  Snippet
                </div>
                <p className="mt-0.5 whitespace-pre-wrap rounded-md border border-slate-100 bg-slate-50 px-3 py-2 text-sm text-slate-700">
                  {ev.snippet || "(no preview available)"}
                </p>
              </div>

              <div>
                <div className="text-[11px] uppercase tracking-wide text-slate-400">
                  Retrieval source
                </div>
                <div className="text-sm text-slate-700">
                  {RETRIEVAL_SOURCE_LABEL[retrievalStatus] ?? "Structured data"}
                </div>
              </div>

              <div>
                <div className="text-[11px] uppercase tracking-wide text-slate-400">
                  Message ID
                </div>
                <div className="mt-0.5 break-all rounded bg-slate-100 px-2 py-1 font-mono text-[11px] text-slate-500">
                  {ev.message_id_header}
                </div>
              </div>
            </div>
          ) : (
            // Degraded: the claim cited this header but no evidence row was
            // returned (e.g. an L1-structured citation with no message snippet).
            <div className="space-y-4">
              <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
                No message preview is available for this citation. The claim still
                references a real source message by its ID below.
              </div>
              <div>
                <div className="text-[11px] uppercase tracking-wide text-slate-400">
                  Message ID
                </div>
                <div className="mt-0.5 break-all rounded bg-slate-100 px-2 py-1 font-mono text-[11px] text-slate-500">
                  {selected.id}
                </div>
              </div>
              <div>
                <div className="text-[11px] uppercase tracking-wide text-slate-400">
                  Retrieval source
                </div>
                <div className="text-sm text-slate-700">
                  {RETRIEVAL_SOURCE_LABEL[retrievalStatus] ?? "Structured data"}
                </div>
              </div>
            </div>
          )
        ) : null}
      </div>
    </aside>
  );
}
