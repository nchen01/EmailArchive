import type { PreflightCheck } from "../api/types";
import { GmailConnectPanel } from "./GmailConnectPanel";
import { GmailDateRangeControl } from "./GmailDateRangeControl";
import { PipelinePanel } from "./PipelinePanel";

interface StatusScreenProps {
  mailboxId: string;
  checks: Record<string, PreflightCheck> | null;
  loading: boolean;
  failed: boolean;
  onRefresh: () => void;
}

const STATUS_META: Record<
  PreflightCheck["status"],
  { dot: string; label: string }
> = {
  pass: { dot: "readiness-dot readiness-ok", label: "Pass" },
  warn: { dot: "readiness-dot readiness-warn", label: "Warn" },
  fail: { dot: "readiness-dot readiness-bad", label: "Fail" },
  info: { dot: "readiness-dot readiness-unknown", label: "Info" },
};

/**
 * Setup / status screen (S12) backed by /api/preflight. Surfaces the full
 * operational check list so an operator can see exactly what is and isn't ready
 * (keys, database, migrations, embeddings, embed-client construction) without
 * reading server logs. Never exposes secret values — preflight messages are
 * already sanitized server-side.
 */
export function StatusScreen({
  mailboxId,
  checks,
  loading,
  failed,
  onRefresh,
}: StatusScreenProps) {
  const rows = checks ? Object.values(checks) : [];

  return (
    <div className="status-screen">
      <header className="status-header">
        <div>
          <h1>Setup &amp; status</h1>
          <p className="status-sub">
            Operational readiness for mailbox{" "}
            <code className="status-mailbox">{mailboxId}</code>.
          </p>
        </div>
        <button
          type="button"
          className="status-refresh"
          onClick={onRefresh}
          disabled={loading}
        >
          {loading ? "Checking…" : "Refresh"}
        </button>
      </header>

      {failed ? (
        <div className="status-failed" role="alert">
          Could not reach the backend preflight endpoint. Start the API
          (scripts/run_backend.ps1) and refresh.
        </div>
      ) : rows.length === 0 ? (
        <p className="overview-section-sub">
          {loading ? "Running checks…" : "No checks available yet."}
        </p>
      ) : (
        <ul className="status-list" role="list">
          {rows.map((c) => {
            const meta = STATUS_META[c.status] ?? STATUS_META.info;
            return (
              <li key={c.name} className="status-row">
                <span className={meta.dot} aria-hidden="true" />
                <span className="status-name">{c.name}</span>
                <span className="status-message">{c.message}</span>
              </li>
            );
          })}
        </ul>
      )}

      <GmailConnectPanel mailboxId={mailboxId} />
      <GmailDateRangeControl mailboxId={mailboxId} />
      <PipelinePanel mailboxId={mailboxId} />
    </div>
  );
}
