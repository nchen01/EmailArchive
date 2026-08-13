import { useState, type ReactNode } from "react";

/** ISO timestamp → compact local string; null/empty → em dash. */
export function fmt(ts: string | null | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const CHIP_TONE: Record<string, string> = {
  // package / provider / job statuses → semantic tone
  published: "bg-jade-soft text-jade",
  connected: "bg-jade-soft text-jade",
  succeeded: "bg-jade-soft text-jade",
  ready: "bg-jade-soft text-jade",
  pass: "bg-jade-soft text-jade",
  running: "bg-brass-soft text-brass",
  queued: "bg-brass-soft text-brass",
  draft: "bg-app2 text-muted",
  superseded: "bg-app2 text-muted",
  disconnected: "bg-app2 text-muted",
  canceled: "bg-app2 text-muted",
  info: "bg-app2 text-muted",
  warn: "bg-warn-soft text-warn",
  partially_succeeded: "bg-warn-soft text-warn",
  refresh_failed: "bg-warn-soft text-warn",
  revoked: "bg-danger-soft text-danger",
  failed: "bg-danger-soft text-danger",
  fail: "bg-danger-soft text-danger",
  degraded: "bg-danger-soft text-danger",
  mismatch_blocked: "bg-danger-soft text-danger",
};

export function StatusChip({ status }: { status: string }) {
  const tone = CHIP_TONE[status] ?? "bg-app2 text-muted";
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-[11px] font-medium ${tone}`}>
      {status}
    </span>
  );
}

/**
 * Render a record of SAFE scalar metadata (audit safe_metadata, job progress).
 * Only primitives + arrays of primitives are shown; objects are collapsed to a
 * neutral marker so a nested content blob can never render. This is defense in
 * depth — the backend already projects these to safe metadata.
 */
export function SafeMeta({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data ?? {});
  if (entries.length === 0) return <span className="text-faint">—</span>;
  return (
    <dl className="grid grid-cols-[auto,1fr] gap-x-3 gap-y-0.5">
      {entries.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="font-mono text-[11px] text-muted">{k}</dt>
          <dd className="font-mono text-[11px] text-ink break-all">{renderScalar(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

function renderScalar(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") {
    return String(v);
  }
  if (Array.isArray(v) && v.every((i) => ["string", "number", "boolean"].includes(typeof i))) {
    return v.join(", ");
  }
  return "(hidden)"; // never render a nested object/blob
}

/**
 * Modal that requires a typed, non-empty reason before an irreversible governance
 * action (revoke / disconnect). Confirm is disabled until the reason is non-blank.
 */
export function ConfirmReasonModal({
  title,
  description,
  confirmLabel,
  busy,
  error,
  onConfirm,
  onClose,
}: {
  title: string;
  description: string;
  confirmLabel: string;
  busy: boolean;
  error: string | null;
  onConfirm: (reason: string) => void;
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const canConfirm = reason.trim().length > 0 && !busy;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
      role="dialog"
      aria-modal="true"
      onClick={busy ? undefined : onClose}
    >
      <div
        className="w-full max-w-md rounded-lg border border-line bg-surface p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        <p className="mt-1 text-xs text-muted">{description}</p>
        <label className="mt-3 block text-xs font-medium text-ink">
          Reason <span className="text-danger">*</span>
        </label>
        <textarea
          className="mt-1 h-20 w-full resize-none rounded-md border border-line2 bg-app px-2 py-1 text-sm text-ink focus:border-brass focus:outline-none"
          value={reason}
          maxLength={500}
          placeholder="Required. Recorded in the audit trail."
          onChange={(e) => setReason(e.target.value)}
          disabled={busy}
          autoFocus
        />
        {error ? <p className="mt-2 text-xs text-danger">{error}</p> : null}
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            className="rounded-md border border-line2 px-3 py-1 text-xs text-ink hover:bg-app2 disabled:opacity-50"
            onClick={onClose}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            type="button"
            className="rounded-md bg-danger px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
            onClick={() => onConfirm(reason.trim())}
            disabled={!canConfirm}
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Small labeled read-only field for detail panels. */
export function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex flex-col">
      <span className="text-[11px] uppercase tracking-wide text-faint">{label}</span>
      <span className="text-sm text-ink break-all">{value ?? "—"}</span>
    </div>
  );
}
