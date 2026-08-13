import { useEffect, useState } from "react";
import {
  disconnectGmail,
  getGmailStatus,
  startGmailConnect,
  type GmailConnectionStatus,
} from "../api/client";

/**
 * Minimal Gmail connection panel (S23). Shows connected/disconnected state and
 * lets the mailbox owner start OAuth or disconnect. Never displays tokens or
 * provider secrets — only the connected account email + status. The dev
 * mailbox-id flow is unaffected; this is additive.
 */
export function GmailConnectPanel({ mailboxId }: { mailboxId: string }) {
  const [status, setStatus] = useState<GmailConnectionStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setError(null);
    getGmailStatus(mailboxId)
      .then(setStatus)
      .catch(() => setStatus({ connected: false }));
  };

  useEffect(load, [mailboxId]);

  // Surface the callback result (safe param only, e.g. ?gmail_connect=connected).
  const result = new URLSearchParams(window.location.search).get("gmail_connect");

  const connect = async () => {
    setBusy(true);
    setError(null);
    try {
      const { authorization_url } = await startGmailConnect(mailboxId);
      window.location.href = authorization_url; // hand off to Google
    } catch {
      setError(
        "Could not start Gmail connect. In production this needs GOOGLE_OAUTH_* configured.",
      );
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true);
    setError(null);
    try {
      await disconnectGmail(mailboxId);
      load();
    } catch {
      setError("Disconnect failed.");
    } finally {
      setBusy(false);
    }
  };

  const connected = status?.connected === true;

  return (
    <section className="mt-6 rounded-md border border-line bg-surface p-4">
      <h2 className="text-sm font-semibold text-ink">Gmail connection</h2>
      <p className="mt-1 text-xs text-muted">
        Connect the mailbox owner&apos;s Gmail with read-only access. Tokens are held
        in the server-side vault, never shown here or stored with the mailbox.
      </p>

      {result === "connected" ? (
        <div className="mt-2 rounded-md border border-jade bg-jade-soft px-3 py-1.5 text-xs text-jade">
          Gmail connected.
        </div>
      ) : result && result !== "connected" ? (
        <div className="mt-2 rounded-md border border-danger-line bg-danger-soft px-3 py-1.5 text-xs text-danger">
          Gmail connect did not complete ({result.replace(/_/g, " ")}).
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-3">
        {connected ? (
          <>
            <span className="text-sm text-ink">
              Connected as{" "}
              <strong>{status?.provider_account_email ?? "(unknown)"}</strong>
            </span>
            <button
              type="button"
              className="rounded-md border border-line2 px-3 py-1.5 text-xs font-medium text-ink hover:bg-app2 disabled:opacity-50"
              onClick={disconnect}
              disabled={busy}
            >
              {busy ? "Working…" : "Disconnect"}
            </button>
          </>
        ) : (
          <>
            <span className="text-sm text-muted">Not connected.</span>
            <button
              type="button"
              className="rounded-md bg-brass px-4 py-1.5 text-sm font-medium text-onbrass hover:bg-brass disabled:bg-brass-soft disabled:text-faint"
              onClick={connect}
              disabled={busy}
            >
              {busy ? "Starting…" : "Connect Gmail"}
            </button>
          </>
        )}
      </div>

      {error ? <p className="mt-2 text-xs text-danger">{error}</p> : null}
    </section>
  );
}
