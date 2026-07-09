import { useState } from "react";
import type { EvidenceSourceType, RetrievalStatus } from "../api/types";
import { formatShortDate } from "../utils/formatDate";

/**
 * The citation-safe fields shared by EvidenceMessage (from a Cover-for-me
 * response) and SourceMessageDetail (from GET /api/source-message). Both drawers
 * render the same view over this shape so the evidence affordance is consistent
 * across surfaces (S14.6).
 */
export interface SourceLike {
  message_id_header: string;
  subject: string;
  date: string; // ISO 8601 ("" if unknown)
  snippet: string;
  sender_display?: string;
  sender_domain?: string;
  source_type?: EvidenceSourceType | null;
  /** Best-effort Gmail rfc822msgid search link; Gmail only, else null/absent. */
  open_url?: string | null;
}

const SOURCE_TYPE_LABEL: Record<EvidenceSourceType, string> = {
  l1_structured: "Structured data",
  l2_retrieval: "Message search",
};

const RETRIEVAL_SOURCE_LABEL: Record<RetrievalStatus, string> = {
  active: "Hybrid retrieval (structured + message search)",
  active_l1_only: "Structured data only",
  disabled_no_key: "Structured data only (message search not configured)",
  degraded_rate_limit: "Structured data only (message search rate-limited)",
  no_embeddings: "Structured data only (no message embeddings)",
  unavailable: "Structured data only (message search unavailable)",
};

function Labelled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-slate-400">{label}</div>
      {children}
    </div>
  );
}

/** Copy-to-clipboard button for the RFC Message-ID (always available — the
 * guaranteed way to find the message even when no deep link exists). */
function CopyMessageId({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard API unavailable (insecure context / permissions): fall back to
      // a transient prompt-free selection cue rather than failing silently.
      setCopied(false);
      window.prompt("Copy this Message-ID:", value);
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      className="mt-1 rounded border border-slate-200 px-2 py-1 text-[11px] font-medium text-slate-600 hover:bg-slate-100"
    >
      {copied ? "Copied ✓" : "Copy message ID"}
    </button>
  );
}

/**
 * Shared, compact detail view for one cited source message. Renders only the
 * safe fields it is given — never fetches bodies, tokens, or provider internals.
 *
 * `retrievalStatus` (Cover-for-me only) adds an answer-level provenance line.
 * `citationCount` shows "Cited in N places" when a header is cited by several
 * claims (S14.4 grouping).
 */
export function SourceDetailView({
  source,
  retrievalStatus,
  citationCount,
}: {
  source: SourceLike;
  retrievalStatus?: RetrievalStatus;
  citationCount?: number;
}) {
  const sender = [source.sender_display, source.sender_domain]
    .filter((s) => s && s.length > 0)
    .join(" · ");

  return (
    <div className="space-y-4">
      <Labelled label="Subject">
        <div className="text-sm font-semibold text-slate-900">
          {source.subject || "(no subject)"}
        </div>
      </Labelled>

      {sender ? (
        <Labelled label="From">
          <div className="text-sm text-slate-700">{sender}</div>
        </Labelled>
      ) : null}

      <Labelled label="Date">
        <div className="text-sm text-slate-700">{formatShortDate(source.date)}</div>
      </Labelled>

      {citationCount && citationCount > 1 ? (
        <Labelled label="Cited">
          <div className="text-sm text-slate-700">
            Cited in {citationCount} places in this answer
          </div>
        </Labelled>
      ) : null}

      <Labelled label="Snippet">
        <p className="mt-0.5 whitespace-pre-wrap rounded-md border border-slate-100 bg-slate-50 px-3 py-2 text-sm text-slate-700">
          {source.snippet || "(no preview available)"}
        </p>
      </Labelled>

      {source.source_type ? (
        <Labelled label="Source">
          <div className="text-sm text-slate-700">
            {SOURCE_TYPE_LABEL[source.source_type]}
          </div>
        </Labelled>
      ) : null}

      {retrievalStatus ? (
        <Labelled label="Retrieval source">
          <div className="text-sm text-slate-700">
            {RETRIEVAL_SOURCE_LABEL[retrievalStatus] ?? "Structured data"}
          </div>
        </Labelled>
      ) : null}

      <Labelled label="Message ID">
        <div className="mt-0.5 break-all rounded bg-slate-100 px-2 py-1 font-mono text-[11px] text-slate-500">
          {source.message_id_header}
        </div>
        <div className="mt-1 flex items-center gap-2">
          <CopyMessageId value={source.message_id_header} />
          {source.open_url ? (
            // Gmail-only, best-effort search link. Labeled "Search in Gmail"
            // (not "open exact email") because it opens a search in whatever
            // account the operator is signed into (D-S14-1).
            <a
              href={source.open_url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-1 rounded border border-slate-200 px-2 py-1 text-[11px] font-medium text-slate-600 hover:bg-slate-100"
              title="Opens a search in your signed-in Gmail"
            >
              Search in Gmail ↗
            </a>
          ) : null}
        </div>
      </Labelled>
    </div>
  );
}
