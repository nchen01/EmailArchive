import { useEffect, useState } from "react";
import {
  createHandoff,
  describeError,
  generateHandoff,
  updateHandoffScope,
} from "../api/client";
import type { HandoffEvidence, HandoffPackage } from "../api/types";

/**
 * Creator scope-review surface (S17.4).
 *
 * The covered employee inspects and prunes a candidate handoff package built from
 * their OWN mailbox before publishing. This is deliberately closer to the old
 * mailbox-review UX (own data), and is NOT the recipient package view. Publish,
 * recipient access, and the scoped recipient artifact are separate later work.
 *
 * No frontend test runner exists in this repo (see docs/s15-verification-matrix.md);
 * verified by `npm run build` + the manual demo in the S17.4 request.
 */
const REASONS = ["vacation", "leave", "transfer", "delegation", "other"];

const KIND_LABEL: Record<string, string> = {
  open_loop: "Open loops",
  decision: "Decisions",
  blocker: "Blockers",
  project_state: "Project state",
  briefing: "Briefing",
  person_note: "People notes",
};
const KIND_ORDER = ["briefing", "project_state", "open_loop", "decision", "blocker", "person_note"];

const EXCLUSION_LABEL: Record<string, string> = {
  sensitivity: "excluded by sensitivity policy",
  user_removed: "removed by you",
  policy_removed: "removed by policy",
  duplicate: "duplicates",
  low_confidence: "low-confidence",
};

function fmtDate(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/** Full current scope as a PATCH body (PATCH replaces the fields it is given). */
function scopeBody(pkg: HandoffPackage) {
  return {
    date_from: pkg.scope.date_from,
    date_to: pkg.scope.date_to,
    included_project_ids: pkg.scope.included_project_ids,
    included_person_ids: pkg.scope.included_person_ids,
    included_thread_ids: pkg.scope.included_thread_ids,
    excluded_thread_ids: pkg.scope.excluded_thread_ids,
    excluded_message_id_headers: pkg.scope.excluded_message_id_headers,
    allowed_domains: pkg.scope.allowed_domains,
    keyword_filters: pkg.scope.keyword_filters,
  };
}

export function HandoffReview({ mailboxId }: { mailboxId: string }) {
  const [pkg, setPkg] = useState<HandoffPackage | null>(null);
  const [reason, setReason] = useState("vacation");
  const [title, setTitle] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState<null | "create" | "scope" | "generate" | "remove">(null);
  const [error, setError] = useState<string | null>(null);

  // A package belongs to one mailbox — reset when the mailbox changes.
  useEffect(() => {
    setPkg(null);
    setError(null);
    setTitle("");
    setFrom("");
    setTo("");
  }, [mailboxId]);

  // Sync date inputs when a new package loads (not on every regenerate edit).
  useEffect(() => {
    if (pkg) {
      setFrom(pkg.scope.date_from ?? "");
      setTo(pkg.scope.date_to ?? "");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pkg?.id]);

  const run = async (kind: typeof busy, fn: () => Promise<HandoffPackage>) => {
    setBusy(kind);
    setError(null);
    try {
      setPkg(await fn());
    } catch (e) {
      setError(describeError(e).message);
    } finally {
      setBusy(null);
    }
  };

  const create = () => run("create", () => createHandoff(mailboxId, { reason, title }));
  const saveScope = () =>
    pkg &&
    run("scope", () =>
      updateHandoffScope(pkg.id, { ...scopeBody(pkg), date_from: from || null, date_to: to || null }),
    );
  const generate = () => pkg && run("generate", () => generateHandoff(pkg.id));

  const removeEvidence = (header: string) =>
    pkg &&
    run("remove", async () => {
      const excluded = [...pkg.scope.excluded_message_id_headers, header];
      await updateHandoffScope(pkg.id, { ...scopeBody(pkg), excluded_message_id_headers: excluded });
      return generateHandoff(pkg.id); // prune + rebuild in one action
    });

  const copyId = (value: string) => {
    navigator.clipboard?.writeText(value).catch(() => window.prompt("Copy this Message-ID:", value));
  };

  const claimsByKind = (kind: string) => (pkg?.claims ?? []).filter((c) => c.kind === kind);
  const excludedHeaders = new Set(pkg?.scope.excluded_message_id_headers ?? []);

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8">
      <h2 className="text-lg font-semibold text-slate-800">Review handoff package</h2>
      <p className="mt-1 text-sm text-slate-500">
        Inspect and prune what a coverage handoff would reveal, from your own
        mailbox, <strong>before publishing</strong>. This is your review surface —
        not the recipient view. Sensitive and noise messages are excluded
        automatically; you can remove anything else. Publishing is a later step.
      </p>

      {error ? (
        <div className="status-failed mt-4" role="alert">
          {error}
        </div>
      ) : null}

      {/* ── Create ─────────────────────────────────────────────────────────── */}
      {!pkg ? (
        <section className="mt-6 rounded-md border border-slate-200 bg-white p-4">
          <h3 className="text-sm font-semibold text-slate-700">Start a handoff</h3>
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <label className="flex flex-col text-xs text-slate-500">
              Reason
              <select
                className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              >
                {REASONS.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-1 flex-col text-xs text-slate-500">
              Title (optional)
              <input
                type="text"
                className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder='e.g. "Covering Atlas while I&apos;m out"'
              />
            </label>
            <button
              type="button"
              className="rounded-md bg-slate-800 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:bg-slate-300"
              onClick={create}
              disabled={busy !== null}
            >
              {busy === "create" ? "Creating…" : "Create draft"}
            </button>
          </div>
        </section>
      ) : (
        <>
          {/* ── Package meta + scope ─────────────────────────────────────── */}
          <section className="mt-6 rounded-md border border-slate-200 bg-white p-4">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-semibold text-slate-800">
                  {pkg.title || "(untitled handoff)"}
                </div>
                <div className="text-xs text-slate-500">
                  reason: {pkg.reason} · status:{" "}
                  <span className="font-medium text-slate-700">{pkg.status}</span> · v{pkg.version}
                </div>
              </div>
              <button
                type="button"
                className="text-xs text-slate-400 hover:text-slate-600"
                onClick={() => setPkg(null)}
              >
                Start over
              </button>
            </div>

            <div className="mt-3 flex flex-wrap items-end gap-3 border-t border-slate-100 pt-3">
              <label className="flex flex-col text-xs text-slate-500">
                From
                <input
                  type="date"
                  className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm"
                  value={from}
                  onChange={(e) => setFrom(e.target.value)}
                />
              </label>
              <label className="flex flex-col text-xs text-slate-500">
                To
                <input
                  type="date"
                  className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm"
                  value={to}
                  onChange={(e) => setTo(e.target.value)}
                />
              </label>
              <button
                type="button"
                className="rounded-md border border-slate-300 px-3 py-2 text-sm hover:bg-slate-50 disabled:opacity-50"
                onClick={saveScope}
                disabled={busy !== null}
              >
                {busy === "scope" ? "Saving…" : "Save scope"}
              </button>
              <button
                type="button"
                className="rounded-md bg-slate-800 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:bg-slate-300"
                onClick={generate}
                disabled={busy !== null}
              >
                {busy === "generate" || busy === "remove" ? "Generating…" : "Generate package"}
              </button>
            </div>
            {(pkg.scope.included_project_ids.length > 0 ||
              pkg.scope.included_person_ids.length > 0 ||
              pkg.scope.included_thread_ids.length > 0) && (
              <div className="mt-2 text-xs text-slate-400">
                Scoped to {pkg.scope.included_project_ids.length} project(s),{" "}
                {pkg.scope.included_person_ids.length} person(s),{" "}
                {pkg.scope.included_thread_ids.length} thread(s).
              </div>
            )}
          </section>

          {/* ── Generated content ────────────────────────────────────────── */}
          {pkg.status === "generated" ? (
            <>
              <ExclusionSummary counts={pkg.exclusion_counts} />

              <section className="mt-4">
                <h3 className="text-sm font-semibold text-slate-700">
                  Claims ({pkg.claims.length})
                </h3>
                {pkg.claims.length === 0 ? (
                  <p className="mt-1 text-sm text-slate-400">
                    No claims in scope yet. Widen the date range or generate again.
                  </p>
                ) : (
                  KIND_ORDER.filter((k) => claimsByKind(k).length > 0).map((kind) => (
                    <div key={kind} className="mt-3">
                      <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                        {KIND_LABEL[kind] ?? kind}
                      </div>
                      <ul className="mt-1 space-y-2">
                        {claimsByKind(kind).map((c) => (
                          <li
                            key={c.id}
                            className="rounded-md border border-slate-100 bg-white px-3 py-2 text-sm"
                          >
                            <div className="text-slate-800">{c.text}</div>
                            <div className="mt-1 flex flex-wrap gap-1">
                              {c.source_message_id_headers.map((h) => (
                                <span
                                  key={h}
                                  className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-500"
                                  title="Cited source message"
                                >
                                  {h}
                                </span>
                              ))}
                            </div>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))
                )}
              </section>

              <section className="mt-6">
                <h3 className="text-sm font-semibold text-slate-700">
                  Evidence ({pkg.evidence.length})
                </h3>
                <p className="text-xs text-slate-400">
                  Snapshotted safe message content. Remove anything that should not
                  travel with the handoff; a claim left without evidence disappears
                  on regenerate.
                </p>
                <ul className="mt-2 space-y-3">
                  {pkg.evidence.map((e) => (
                    <EvidenceCard
                      key={e.message_id_header}
                      ev={e}
                      removing={excludedHeaders.has(e.message_id_header)}
                      onRemove={() => removeEvidence(e.message_id_header)}
                      onCopy={() => copyId(e.message_id_header)}
                      disabled={busy !== null}
                    />
                  ))}
                  {pkg.evidence.length === 0 ? (
                    <li className="text-sm text-slate-400">No evidence in scope.</li>
                  ) : null}
                </ul>
              </section>
            </>
          ) : (
            <p className="mt-4 text-sm text-slate-500">
              Set a scope (optional) and click <strong>Generate package</strong> to
              build the candidate for review.
            </p>
          )}
        </>
      )}
    </div>
  );
}

function ExclusionSummary({ counts }: { counts: Record<string, number> }) {
  const parts = Object.entries(counts)
    .filter(([, n]) => n > 0)
    .map(([type, n]) => `${n} ${EXCLUSION_LABEL[type] ?? type}`);
  if (parts.length === 0) return null;
  return (
    <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
      Withheld from this package (visible to you only, never to the recipient):{" "}
      {parts.join(" · ")}.
    </div>
  );
}

function EvidenceCard({
  ev,
  removing,
  onRemove,
  onCopy,
  disabled,
}: {
  ev: HandoffEvidence;
  removing: boolean;
  onRemove: () => void;
  onCopy: () => void;
  disabled: boolean;
}) {
  const from = [ev.sender_display, ev.sender_domain].filter(Boolean).join(" · ");
  return (
    <li className="rounded-md border border-slate-200 bg-white p-3 text-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-semibold text-slate-900">{ev.subject || "(no subject)"}</div>
          <div className="text-xs text-slate-500">
            {from || "—"} · {fmtDate(ev.date)}
          </div>
        </div>
        <button
          type="button"
          className="shrink-0 rounded border border-slate-200 px-2 py-1 text-[11px] font-medium text-red-600 hover:bg-red-50 disabled:opacity-50"
          onClick={onRemove}
          disabled={disabled}
          title="Exclude this message and regenerate"
        >
          {removing ? "Removing…" : "Remove"}
        </button>
      </div>
      {ev.body_snapshot ? (
        <p className="mt-2 whitespace-pre-wrap rounded bg-slate-50 px-2 py-1.5 text-[13px] text-slate-700">
          {ev.body_snapshot}
        </p>
      ) : null}
      <div className="mt-1 flex items-center gap-2">
        <span className="break-all rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-500">
          {ev.message_id_header}
        </span>
        <button
          type="button"
          className="rounded border border-slate-200 px-2 py-0.5 text-[10px] text-slate-500 hover:bg-slate-100"
          onClick={onCopy}
        >
          Copy ID
        </button>
      </div>
    </li>
  );
}
