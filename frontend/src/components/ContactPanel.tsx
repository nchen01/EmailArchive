import { useEffect, useState } from "react";
import {
  fetchContactSummary,
  SummariesNotConfiguredError,
} from "../api/client";
import type { ContactDetail, SynthesisResult } from "../api/types";
import { roleColor, roleLabel } from "../utils/roleColors";
import { LoadingSpinner } from "./LoadingSpinner";

interface ContactPanelProps {
  detail: ContactDetail | null;
  loading: boolean;
  error: string | null;
  open: boolean;
  onClose: () => void;
  /** Mailbox + person ids, needed to trigger L3 synthesis (spec 05 §3.4). */
  mailboxId: string | null;
  personId: string | null;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-md bg-slate-50 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-slate-400">
        {label}
      </div>
      <div className="text-sm font-semibold text-slate-800">{value}</div>
    </div>
  );
}

export function ContactPanel({
  detail,
  loading,
  error,
  open,
  onClose,
  mailboxId,
  personId,
}: ContactPanelProps) {
  // L3 "Ask about this contact" synthesis state.
  const [summary, setSummary] = useState<SynthesisResult | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  // Reset the synthesis result whenever the selected contact changes.
  useEffect(() => {
    setSummary(null);
    setSummaryError(null);
    setSummaryLoading(false);
  }, [personId]);

  const askAboutContact = async () => {
    if (!mailboxId || !personId) return;
    setSummaryLoading(true);
    setSummaryError(null);
    setSummary(null);
    try {
      const res = await fetchContactSummary(mailboxId, personId);
      setSummary(res);
    } catch (err) {
      if (err instanceof SummariesNotConfiguredError) {
        setSummaryError("Summaries are not configured.");
      } else {
        setSummaryError("Could not generate summary. Please try again.");
      }
    } finally {
      setSummaryLoading(false);
    }
  };

  return (
    <aside
      className={`fixed right-0 top-0 z-20 flex h-full w-[380px] flex-col border-l border-slate-200 bg-white shadow-xl transition-transform duration-300 ${
        open ? "translate-x-0" : "translate-x-full"
      }`}
      aria-hidden={!open}
    >
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <h2 className="text-sm font-semibold text-slate-600">Contact detail</h2>
        <button
          type="button"
          onClick={onClose}
          className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
          aria-label="Close panel"
        >
          ✕
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {loading ? <LoadingSpinner label="Loading contact…" /> : null}

        {error && !loading ? (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
            {error}
          </div>
        ) : null}

        {detail && !loading ? (
          <div className="space-y-5">
            {/* Header */}
            <div>
              <h3 className="text-lg font-semibold text-slate-900">
                {detail.person.name}
              </h3>
              <div className="text-sm text-slate-500">
                {detail.person.canonical_email}
              </div>
              <div className="mt-2 flex items-center gap-2">
                <span
                  className="rounded-full px-2 py-0.5 text-xs font-medium text-white"
                  style={{ backgroundColor: roleColor(detail.person.role) }}
                >
                  {roleLabel(detail.person.role)}
                </span>
                {detail.person.org_domain ? (
                  <span className="text-xs text-slate-400">
                    {detail.person.org_domain}
                  </span>
                ) : null}
              </div>

              <div className="mt-3">
                <div className="mb-1 flex justify-between text-[11px] text-slate-400">
                  <span>Role confidence</span>
                  <span>
                    {Math.round(detail.person.role_confidence * 100)}%
                  </span>
                </div>
                <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${Math.min(
                        100,
                        Math.max(0, detail.person.role_confidence * 100),
                      )}%`,
                      backgroundColor: roleColor(detail.person.role),
                    }}
                  />
                </div>
              </div>

              {detail.person.all_emails.length > 1 ? (
                <div className="mt-3 text-xs text-slate-500">
                  <div className="mb-1 font-medium text-slate-400">
                    All addresses
                  </div>
                  <ul className="space-y-0.5">
                    {detail.person.all_emails.map((e) => (
                      <li key={e}>{e}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>

            {/* Relationship stats */}
            <div>
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                Relationship
              </h4>
              <div className="grid grid-cols-2 gap-2">
                <Stat label="Messages" value={detail.edge.message_count} />
                <Stat label="Strength" value={detail.edge.weight.toFixed(2)} />
                <Stat label="Sent to" value={detail.edge.sent_to_count} />
                <Stat label="Received" value={detail.edge.received_count} />
                <Stat
                  label="First contact"
                  value={formatDate(detail.edge.first_contact)}
                />
                <Stat
                  label="Last contact"
                  value={formatDate(detail.edge.last_contact)}
                />
              </div>
            </div>

            {/* Recent threads */}
            <div>
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                Recent threads ({detail.recent_threads.length})
              </h4>
              {detail.recent_threads.length === 0 ? (
                <div className="text-sm text-slate-400">No shared threads.</div>
              ) : (
                <ul className="space-y-2">
                  {detail.recent_threads.map((t) => (
                    <li
                      key={t.thread_id}
                      className="rounded-md border border-slate-100 px-3 py-2"
                    >
                      <div className="text-sm font-medium text-slate-800">
                        {t.subject || "(no subject)"}
                      </div>
                      <div className="mt-0.5 flex justify-between text-xs text-slate-400">
                        <span>{formatDate(t.last)}</span>
                        <span>
                          {t.other_participants.length} other
                          {t.other_participants.length === 1 ? "" : "s"}
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {/* Ask about this contact (L3 synthesis, spec 05 §3.4) */}
            <div className="space-y-2 pt-2">
              <button
                type="button"
                onClick={askAboutContact}
                disabled={summaryLoading || !mailboxId || !personId}
                className="w-full rounded-md bg-slate-800 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
              >
                {summaryLoading ? "Asking…" : "Ask about this contact"}
              </button>

              {summaryLoading ? (
                <LoadingSpinner label="Summarizing…" />
              ) : null}

              {summaryError ? (
                <div
                  className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
                  role="alert"
                >
                  {summaryError}
                </div>
              ) : null}

              {summary && !summaryLoading ? (
                <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
                  {summary.claims.length === 0 ? (
                    <p className="text-slate-400">
                      {summary.state ?? "No evidenced activity in email."}
                    </p>
                  ) : (
                    <ul className="space-y-2">
                      {summary.claims.map((c, i) => (
                        <li key={i}>
                          <div>{c.text}</div>
                          <div className="mt-0.5 flex flex-wrap gap-1">
                            {c.source_message_ids.map((id) => (
                              <span
                                key={id}
                                title={id}
                                className="rounded bg-slate-200 px-1.5 py-0.5 text-[10px] text-slate-600"
                              >
                                {id}
                              </span>
                            ))}
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>
    </aside>
  );
}
