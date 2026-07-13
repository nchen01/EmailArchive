import { useState } from "react";
import { describeError, ingestGmailWindow, previewGmailWindow } from "../api/client";
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
  const [preview, setPreview] = useState<GmailWindowResponse | null>(null);
  const [ingested, setIngested] = useState<GmailWindowResponse | null>(null);
  const [busy, setBusy] = useState<null | "preview" | "ingest">(null);
  const [error, setError] = useState<string | null>(null);

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
    setIngested(null);
    setError(null);
  };

  const doPreview = async () => {
    setError(null);
    setIngested(null);
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
    try {
      setIngested(await ingestGmailWindow(mailboxId, { ...req(), confirm: true }));
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
          {busy === "ingest" ? "Ingesting…" : "Confirm ingest"}
        </button>
      </div>

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

      {preview && !ingested ? (
        <div className="status-list" style={{ marginTop: "0.75rem", fontSize: "0.85rem" }}>
          <div>
            Window: <strong>{preview.date_from ?? "(open)"}</strong> –{" "}
            <strong>{preview.date_to ?? "(open)"}</strong>
            {preview.provider_filter_applied ? " (filter applied)" : " (open — whole mailbox)"}
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
            Ready to persist this window — click <strong>Confirm ingest</strong> (scoped snapshot).
          </div>
        </div>
      ) : null}

      {ingested ? (
        <div className="status-list" style={{ marginTop: "0.75rem", fontSize: "0.85rem" }}>
          <div>
            Ingested <strong>{ingested.count}</strong> messages for window{" "}
            {ingested.date_from ?? "(open)"} – {ingested.date_to ?? "(open)"}.
          </div>
          <div>Sync token: {ingested.sync_token_disposition}.</div>
          {ingested.cap_hit ? (
            <div style={{ color: "#b45309" }}>
              Cap was hit — snapshot may be incomplete; narrow the range or raise the cap.
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
