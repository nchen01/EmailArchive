import { useCallback, useEffect, useState } from "react";
import {
  adminRevokePackage,
  describeError,
  getAdminPackage,
  getAdminPackageAudit,
  listAdminPackages,
} from "../../api/client";
import type {
  PackageAdminDetail,
  PackageAdminSummary,
  PackageAuditEventView,
} from "../../api/types";
import { ConfirmReasonModal, Field, fmt, SafeMeta, StatusChip } from "./ui";

/**
 * Packages panel: lifecycle metadata list + a detail/audit side panel, plus the
 * admin revoke action (typed-reason confirmation). Safe metadata ONLY — never
 * claim text, evidence bodies, scope detail, or source headers.
 */
export function AdminPackages() {
  const [rows, setRows] = useState<PackageAdminSummary[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<PackageAdminDetail | null>(null);
  const [audit, setAudit] = useState<PackageAuditEventView[] | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [revoking, setRevoking] = useState(false);
  const [showRevoke, setShowRevoke] = useState(false);
  const [revokeError, setRevokeError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  const loadList = useCallback(() => {
    setListError(null);
    listAdminPackages()
      .then(setRows)
      .catch((e) => setListError(describeError(e).message));
  }, []);

  useEffect(() => {
    loadList();
  }, [loadList]);

  const openDetail = useCallback((id: string) => {
    setSelectedId(id);
    setDetail(null);
    setAudit(null);
    setDetailError(null);
    setFlash(null);
    Promise.all([getAdminPackage(id), getAdminPackageAudit(id)])
      .then(([d, a]) => {
        setDetail(d);
        setAudit(a);
      })
      .catch((e) => setDetailError(describeError(e).message));
  }, []);

  const doRevoke = async (reason: string) => {
    if (!selectedId) return;
    setRevoking(true);
    setRevokeError(null);
    try {
      const updated = await adminRevokePackage(selectedId, reason);
      setDetail(updated);
      setShowRevoke(false);
      setFlash("Package revoked. Recipient access is now blocked.");
      loadList();
      getAdminPackageAudit(selectedId).then(setAudit).catch(() => undefined);
    } catch (e) {
      setRevokeError(describeError(e).message);
    } finally {
      setRevoking(false);
    }
  };

  return (
    <div className="flex h-full gap-4">
      {/* List */}
      <div className="w-1/2 min-w-0 overflow-y-auto">
        {listError ? (
          <p className="text-xs text-danger">{listError}</p>
        ) : rows === null ? (
          <p className="text-xs text-muted">Loading packages…</p>
        ) : rows.length === 0 ? (
          <p className="text-xs text-muted">No packages in this tenant.</p>
        ) : (
          <table className="w-full text-left text-xs">
            <thead className="text-faint">
              <tr>
                <th className="py-1 pr-2 font-medium">Title</th>
                <th className="py-1 pr-2 font-medium">Status</th>
                <th className="py-1 pr-2 font-medium">Ver</th>
                <th className="py-1 font-medium">Created</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr
                  key={p.id}
                  className={`cursor-pointer border-t border-line hover:bg-app2 ${
                    selectedId === p.id ? "bg-app2" : ""
                  }`}
                  onClick={() => openDetail(p.id)}
                >
                  <td className="py-1.5 pr-2 text-ink">{p.title || "(untitled)"}</td>
                  <td className="py-1.5 pr-2"><StatusChip status={p.status} /></td>
                  <td className="py-1.5 pr-2 text-muted">v{p.version}</td>
                  <td className="py-1.5 text-muted">{fmt(p.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Detail + audit */}
      <div className="w-1/2 min-w-0 overflow-y-auto border-l border-line pl-4">
        {!selectedId ? (
          <p className="text-xs text-faint">Select a package to see its lifecycle metadata.</p>
        ) : detailError ? (
          <p className="text-xs text-danger">{detailError}</p>
        ) : !detail ? (
          <p className="text-xs text-muted">Loading detail…</p>
        ) : (
          <div className="space-y-4">
            {flash ? (
              <p className="rounded-md border border-jade bg-jade-soft px-3 py-2 text-xs text-jade">{flash}</p>
            ) : null}
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-sm font-semibold text-ink">{detail.title || "(untitled)"}</h3>
              <StatusChip status={detail.status} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Status" value={detail.status} />
              <Field label="Version" value={`v${detail.version}`} />
              <Field label="Reason category" value={detail.reason_category} />
              <Field label="Policy mode" value={detail.policy_mode} />
              <Field label="Creator" value={detail.creator_email} />
              <Field label="Recipient" value={detail.recipient_email ?? "—"} />
              <Field label="Recipient state" value={detail.recipient_state ?? "—"} />
              <Field label="Claims / Evidence" value={`${detail.claim_count} / ${detail.evidence_count}`} />
              <Field label="Created" value={fmt(detail.created_at)} />
              <Field label="Published" value={fmt(detail.published_at)} />
              <Field label="Expires" value={fmt(detail.expires_at)} />
              <Field label="Revoked" value={fmt(detail.revoked_at)} />
              <Field label="Exported" value={fmt(detail.exported_at)} />
            </div>

            {detail.status === "published" ? (
              <button
                type="button"
                className="rounded-md border border-danger-line bg-danger-soft px-3 py-1 text-xs font-medium text-danger hover:opacity-90"
                onClick={() => {
                  setRevokeError(null);
                  setShowRevoke(true);
                }}
              >
                Revoke package…
              </button>
            ) : (
              <p className="text-[11px] text-faint">
                Revoke is available only for a published package (current status: {detail.status}).
              </p>
            )}

            <div>
              <h4 className="mb-1 text-xs font-semibold text-ink">Audit trail</h4>
              {audit === null ? (
                <p className="text-xs text-muted">Loading…</p>
              ) : audit.length === 0 ? (
                <p className="text-xs text-faint">No audit events.</p>
              ) : (
                <ul className="space-y-1.5">
                  {audit.map((e, i) => (
                    <li key={i} className="rounded-md border border-line bg-surface p-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono text-[11px] text-ink">{e.action}</span>
                        <span className="text-[11px] text-faint">{fmt(e.ts)}</span>
                      </div>
                      <div className="text-[11px] text-muted">actor: {e.actor}</div>
                      {Object.keys(e.safe_metadata ?? {}).length > 0 ? (
                        <div className="mt-1"><SafeMeta data={e.safe_metadata} /></div>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </div>

      {showRevoke ? (
        <ConfirmReasonModal
          title="Revoke this package?"
          description="Blocks the recipient's access immediately and kills any live session. This cannot be undone; a new version must be published to re-share."
          confirmLabel="Revoke package"
          busy={revoking}
          error={revokeError}
          onConfirm={doRevoke}
          onClose={() => setShowRevoke(false)}
        />
      ) : null}
    </div>
  );
}
