import { useRef, useState } from "react";
import {
  describeError,
  getJob,
  ingestGmailWindow,
  isJobTerminal,
  previewGmailWindow,
  type JobView,
} from "../api/client";
import type { GmailWindowResponse } from "../api/types";

/**
 * Demo-side date-window control for Gmail ingest (S16.0, D-S16.0-8/-9).
 *
 * Lets an operator choose a customizable date window, PREVIEW how many messages
 * match (no bodies fetched, nothing persisted), then run a confirm-gated scoped
 * snapshot ingest. It never asks the browser for an OAuth token — the backend
 * uses its own environment credential. This is a bounded operator control, not a
 * production onboarding wizard.
 *
 * Client-side validation blocks an inverted window (start after end) BEFORE any
 * backend call; the backend re-validates with the same rules (parse_date_window).
 *
 * No frontend test runner exists in this repo (see docs/s15-verification-matrix.md);
 * verified by `npm run build` + manual walk-through.
 */
function validateWindow(from: string, to: string): string | null {
  // Native <input type="date"> yields YYYY-MM-DD (or ""), so format is enforced
  // by the control; the one client-checkable range error is start-after-end.
  if (from && to && from > to) {
    return "Start date must not be after end date.";
  }
  return null;
}

export function GmailDateRangeControl({ mailboxId }: { mailboxId: string }) {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [maxMessages, setMaxMessages] = useState(500);
  const [replace, setReplace] = useState(false);
  const [preview, setPreview] = useState<GmailWindowResponse | null>(null);
  const [job, setJob] = useState<JobView | null>(null); // S25: ingest job status
  const [busy, setBusy] = useState<null | "preview" | "ingest">(null);
  const [error, setError] = useState<string | null>(null);
  const pollTimer = useRef<number | null>(null);

  const clientError = validateWindow(from, to);
  // A live ingest is gated behind a successful preview of the current window.
  const canIngest = preview !== null && !clientError && busy === null;

  const req = () => ({
    date_from: from || null,
    date_to: to || null,
    max_messages: maxMessages,
  });

  const onFieldChange = () => {
    // Changing the window invalidates a prior preview so ingest can't run stale.
    setPreview(null);
    setJob(null);
    setError(null);
    if (pollTimer.current) window.clearTimeout(pollTimer.current);
  };

  // S25: poll the ingest job until it reaches a terminal state.
  const pollJob = (jobId: string) => {
    getJob(jobId)
      .then((j) => {
        setJob(j);
        if (!isJobTerminal(j.status)) {
          pollTimer.current = window.setTimeout(() => pollJob(jobId), 1500);
        }
      })
      .catch((e) => setError(describeError(e).message));
  };

  const doPreview = async () => {
    setError(null);
    setJob(null);
    if (clientError) {
      setError(clientError); // block before any backend call
      return;
    }
    setBusy("preview");
    try {
      setPreview(await previewGmailWindow(mailboxId, req()));
    } catch (e) {
      setPreview(null);
      setError(describeError(e).message);
    } finally {
      setBusy(null);
    }
  };

  const doIngest = async () => {
    setError(null);
    if (clientError) {
      setError(clientError);
      return;
    }
    setBusy("ingest");
    setJob(null);
    try {
      // replace_snapshot is sent ONLY when the operator explicitly enabled it.
      // S25: this enqueues a background job; poll it for status/progress.
      const ref = await ingestGmailWindow(mailboxId, {
        ...req(),
        confirm: true,
        replace_snapshot: replace,
      });
      setJob({ id: ref.job_id, status: ref.status, progress: {}, summary: null, error_category: null });
      pollJob(ref.job_id);
    } catch (e) {
      setError(describeError(e).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="daterange-control" style={{ marginTop: "1.5rem" }}>
      <h2 style={{ fontSize: "0.95rem", fontWeight: 600 }}>
        Date-windowed Gmail ingest (operator)
      </h2>
      <p className="overview-section-sub">
        Choose a received-date window, preview the match count, then run a scoped
        snapshot. A date-windowed run is a <strong>scoped snapshot</strong>: no
        sync token is saved, so it does not become an incremental baseline. Dates
        are inclusive; leave a field blank for an open bound.
      </p>

      <div
        style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap", alignItems: "flex-end", marginTop: "0.75rem" }}
      >
        <label style={{ display: "flex", flexDirection: "column", fontSize: "0.75rem" }}>
          From (inclusive)
          <input
            type="date"
            value={from}
            onChange={(e) => { setFrom(e.target.value); onFieldChange(); }}
            className="daterange-input"
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", fontSize: "0.75rem" }}>
          To (inclusive)
          <input
            type="date"
            value={to}
            onChange={(e) => { setTo(e.target.value); onFieldChange(); }}
            className="daterange-input"
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", fontSize: "0.75rem" }}>
          Max messages (cap)
          <input
            type="number"
            min={1}
            value={maxMessages}
            onChange={(e) => { setMaxMessages(Math.max(1, Number(e.target.value) || 1)); onFieldChange(); }}
            className="daterange-input"
            style={{ width: "8rem" }}
          />
        </label>
        <button
          type="button"
          className="status-refresh"
          onClick={doPreview}
          disabled={busy !== null || !!clientError}
        >
          {busy === "preview" ? "Previewing…" : "Preview window"}
        </button>
        <button
          type="button"
          className="status-refresh"
          onClick={doIngest}
          disabled={!canIngest}
          title={preview === null ? "Preview the window first" : undefined}
        >
          {busy === "ingest"
            ? "Ingesting…"
            : replace
              ? "Replace + ingest"
              : "Confirm ingest"}
        </button>
      </div>

      <label
        style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginTop: "0.75rem", fontSize: "0.85rem" }}
      >
        <input
          type="checkbox"
          checked={replace}
          onChange={(e) => { setReplace(e.target.checked); setJob(null); }}
        />
        Replace current mailbox snapshot
      </label>
      {replace ? (
        <div className="status-failed" role="alert" style={{ marginTop: "0.5rem" }}>
          <strong>Destructive:</strong> This removes existing messages and derived
          data for this mailbox before ingesting the selected date window. Use this
          when preparing a clean demo workspace.
        </div>
      ) : (
        <p className="overview-section-sub" style={{ marginTop: "0.5rem" }}>
          Append/upsert: adds or updates messages in the window and preserves
          existing out-of-window data. Enable “Replace” for a clean workspace.
        </p>
      )}

      {clientError ? (
        <p role="alert" style={{ color: "#b45309", marginTop: "0.5rem", fontSize: "0.85rem" }}>
          {clientError}
        </p>
      ) : null}

      {error ? (
        <div className="status-failed" role="alert" style={{ marginTop: "0.75rem" }}>
          {error}
        </div>
      ) : null}

      {preview && !job ? (
        <div className="status-list" style={{ marginTop: "0.75rem", fontSize: "0.85rem" }}>
          <div>
            Window: <strong>{preview.date_from ?? "(open)"}</strong> –{" "}
            <strong>{preview.date_to ?? "(open)"}</strong>
            {preview.provider_filter_applied ? " (filter applied)" : " (open: whole mailbox)"}
          </div>
          <div>
            Matching messages:{" "}
            <strong>
              {preview.cap_hit ? `≥ ${preview.count} (capped at max)` : `${preview.count} (exact)`}
            </strong>
          </div>
          <div>No bodies fetched · nothing persisted · sync token not saved (preview).</div>
          {preview.cap_hit ? (
            <div style={{ color: "#b45309" }}>
              Narrow the date range or raise the cap to see the full count.
            </div>
          ) : null}
          <div style={{ marginTop: "0.25rem" }}>
            Ready to persist this window (scoped snapshot):{" "}
            {replace
              ? "will REPLACE the current mailbox snapshot before ingesting."
              : "append/upsert; existing out-of-window data is preserved."}
          </div>
        </div>
      ) : null}

      {job ? (
        <div className="status-list" style={{ marginTop: "0.75rem", fontSize: "0.85rem" }}>
          <div>
            Ingest job: <strong>{job.status}</strong>
            {!isJobTerminal(job.status) ? " · running in the background…" : null}
          </div>
          {typeof job.progress?.phase === "string" ? (
            <div>Phase: {String(job.progress.phase)}</div>
          ) : null}
          {job.status === "succeeded" ? (
            <div>
              Ingested <strong>{String(job.progress?.messages ?? "?")}</strong> messages
              {job.progress?.replaced ? " (replaced snapshot)" : ""}. Sync token not saved.
            </div>
          ) : null}
          {job.status === "failed" ? (
            <div style={{ color: "#a9412b" }}>
              Ingest failed ({job.error_category ?? "error"}). No secrets or content are logged.
            </div>
          ) : null}
          <div style={{ color: "var(--faint)" }}>
            Runs on the background worker (scripts/run_worker.py); track it on the jobs API.
          </div>
        </div>
      ) : null}
    </section>
  );
}
