import { useCallback, useEffect, useState } from "react";
import {
  adminDisconnectProviderAccount,
  describeError,
  listAdminProviderAccounts,
} from "../../api/client";
import type { ProviderAccountAdminView } from "../../api/types";
import { ConfirmReasonModal, fmt, StatusChip } from "./ui";

/**
 * Provider accounts panel: connection status metadata + the admin disconnect
 * action (typed reason). Security-reviewer principals see provider/status/
 * timestamps only (the API nulls the ids/email/scopes) — we render whatever the
 * API returns and never reconstruct hidden fields. Disconnect fails closed: a
 * 503 leaves the account unchanged.
 */
export function AdminProviders() {
  const [rows, setRows] = useState<ProviderAccountAdminView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [target, setTarget] = useState<ProviderAccountAdminView | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    listAdminProviderAccounts()
      .then(setRows)
      .catch((e) => setError(describeError(e).message));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const doDisconnect = async (reason: string) => {
    if (!target?.id) return;
    setBusy(true);
    setActionError(null);
    try {
      await adminDisconnectProviderAccount(target.id, reason);
      setTarget(null);
      setFlash("Provider account disconnected — token revoked and vault purged.");
      load();
    } catch (e) {
      setActionError(describeError(e).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto">
      {flash ? (
        <p className="mb-3 rounded-md border border-jade bg-jade-soft px-3 py-2 text-xs text-jade">{flash}</p>
      ) : null}
      {error ? (
        <p className="text-xs text-danger">{error}</p>
      ) : rows === null ? (
        <p className="text-xs text-muted">Loading provider accounts…</p>
      ) : rows.length === 0 ? (
        <p className="text-xs text-muted">No provider accounts in this tenant.</p>
      ) : (
        <table className="w-full text-left text-xs">
          <thead className="text-faint">
            <tr>
              <th className="py-1 pr-2 font-medium">Provider</th>
              <th className="py-1 pr-2 font-medium">Account</th>
              <th className="py-1 pr-2 font-medium">Status</th>
              <th className="py-1 pr-2 font-medium">Connected</th>
              <th className="py-1 pr-2 font-medium">Scopes</th>
              <th className="py-1 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a, i) => {
              const live = a.status === "connected" || a.status === "refresh_failed";
              return (
                <tr key={a.id ?? i} className="border-t border-line">
                  <td className="py-1.5 pr-2 text-ink">{a.provider}</td>
                  <td className="py-1.5 pr-2 text-muted">{a.provider_account_email ?? "—"}</td>
                  <td className="py-1.5 pr-2"><StatusChip status={a.status} /></td>
                  <td className="py-1.5 pr-2 text-muted">{fmt(a.connected_at)}</td>
                  <td className="py-1.5 pr-2 text-muted">{a.scopes_granted.length || "—"}</td>
                  <td className="py-1.5 text-right">
                    {live && a.id ? (
                      <button
                        type="button"
                        className="rounded-md border border-danger-line bg-danger-soft px-2 py-0.5 text-[11px] font-medium text-danger hover:opacity-90"
                        onClick={() => {
                          setActionError(null);
                          setFlash(null);
                          setTarget(a);
                        }}
                      >
                        Disconnect…
                      </button>
                    ) : (
                      <span className="text-[11px] text-faint">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <p className="mt-3 text-[11px] text-faint">
        Disconnect revokes the provider token and purges the vault entry, then marks the account
        disconnected. It never reconnects or reveals a token. Fails closed if the vault is unavailable.
      </p>

      {target ? (
        <ConfirmReasonModal
          title="Disconnect this provider account?"
          description="Revokes the provider-side token and purges the vault entry, then marks the account disconnected. The owner must reconnect it themselves; there is no silent reconnect."
          confirmLabel="Disconnect account"
          busy={busy}
          error={actionError}
          onConfirm={doDisconnect}
          onClose={() => setTarget(null)}
        />
      ) : null}
    </div>
  );
}
