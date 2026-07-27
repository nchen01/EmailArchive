import type { EvidenceMessage, RetrievalStatus } from "../api/types";
import { SourceDetailView } from "./SourceDetail";

/** The citation the user clicked: the cited header plus its evidence (if any). */
export interface SelectedCitation {
  /** message_id_header from the claim's source_message_ids. */
  id: string;
  /** Matching supporting_evidence row, or undefined when none was returned. */
  evidence: EvidenceMessage | undefined;
  /** How many claims in this answer cite this header (S14.4 grouping). */
  citationCount?: number;
}

interface EvidenceDrawerProps {
  selected: SelectedCitation | null;
  /** Answer-level retrieval status, shown as provenance context. */
  retrievalStatus: RetrievalStatus;
  onClose: () => void;
}

/**
 * Slide-in drawer that lets a user inspect *why* a Cover-for-me claim was made
 * by opening the cited source message's metadata: subject, sender, date, the
 * normalized snippet, the source layer, the RFC 5322 message_id_header (with a
 * copy action), and — for Gmail mailboxes — a best-effort "Search in Gmail" link.
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
      className={`fixed right-0 top-0 z-30 flex h-full w-[380px] flex-col border-l border-line bg-surface shadow-xl transition-transform duration-300 ${
        open ? "translate-x-0" : "translate-x-full"
      }`}
      aria-hidden={!open}
    >
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 className="text-sm font-semibold text-muted">Citation detail</h2>
        <button
          type="button"
          onClick={onClose}
          className="rounded p-1 text-faint hover:bg-app2 hover:text-muted"
          aria-label="Close citation detail"
        >
          ✕
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {selected ? (
          ev ? (
            <SourceDetailView
              source={ev}
              retrievalStatus={retrievalStatus}
              citationCount={selected.citationCount}
            />
          ) : (
            // Degraded: the claim cited this header but no evidence row was
            // returned (e.g. an L1-structured citation with no message snippet).
            <div className="space-y-4">
              <div className="rounded-md border border-line bg-app2 px-3 py-2 text-sm text-muted">
                No message preview is available for this citation. The claim still
                references a real source message by its ID below.
              </div>
              <SourceDetailView
                source={{
                  message_id_header: selected.id,
                  subject: "",
                  date: "",
                  snippet: "",
                }}
                retrievalStatus={retrievalStatus}
              />
            </div>
          )
        ) : null}
      </div>
    </aside>
  );
}
