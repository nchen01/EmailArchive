import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  ApiError,
  describeError,
  getAdminExclusionSummary,
  getAdminOverview,
  getAdminReadiness,
  listAdminAudit,
  listAdminJobs,
} from "../../api/client";
import type {
  AuditEventView,
  ExclusionSummaryView,
  JobAdminView,
  ReadinessSummaryView,
  TenantOpsOverview,
} from "../../api/types";
import { AdminPackages } from "./AdminPackages";
import { AdminProviders } from "./AdminProviders";
import { fmt, SafeMeta, StatusChip } from "./ui";

/**
 * Read-only Admin / Audit Viewer console (S31) over the S29/S30 /api/admin/*
 * endpoints. Tenant-scoped, safe metadata only — this UI can never show mailbox
 * content, evidence bodies, tokens, or vault refs. Two audited actions (revoke
 * package, disconnect provider) live in their panels behind a typed-reason modal.
 *
 * Tenant-scoped, NOT mailbox-scoped: it needs no mailbox id and renders whatever
 * the caller's role is allowed to see (the API does the masking).
 */
type Tab = "overview" | "packages" | "providers" | "jobs" | "audit" | "exclusions";

const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "packages", label: "Packages" },
  { id: "providers", label: "Providers" },
  { id: "jobs", label: "Jobs" },
  { id: "audit", label: "Audit log" },
  { id: "exclusions", label: "Exclusions" },
];

export function AdminConsole() {
  const [tab, setTab] = useState<Tab>("overview");
  return (
    <div className="mx-auto flex h-full max-w-5xl flex-col px-6 py-5">
      <div className="mb-1 flex items-baseline justify-between">
        <h1 className="text-base font-semibold text-ink">Admin &amp; Audit</h1>
        <span className="text-[11px] text-faint">Tenant governance · safe metadata only</span>
      </div>
      <nav className="mb-4 flex gap-1 border-b border-line">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={`-mb-px border-b-2 px-3 py-1.5 text-xs font-medium ${
              tab === t.id
                ? "border-brass text-ink"
                : "border-transparent text-muted hover:text-ink"
            }`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <div className="min-h-0 flex-1">
        {tab === "overview" ? (
          <OverviewPanel />
        ) : tab === "packages" ? (
          <AdminPackages />
        ) : tab === "providers" ? (
          <AdminProviders />
        ) : tab === "jobs" ? (
          <JobsPanel />
        ) : tab === "audit" ? (
          <AuditPanel />
        ) : (
          <ExclusionsPanel />
        )}
      </div>
    </div>
  );
}

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-md border border-line bg-surface p-4">
      <h2 className="mb-2 text-xs font-semibold text-ink">{title}</h2>
      {children}
    </section>
  );
}

function CountRow({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts ?? {});
  if (entries.length === 0) return <span className="text-xs text-faint">none</span>;
  return (
    <div className="flex flex-wrap gap-2">
      {entries.map(([k, n]) => (
        <span key={k} className="flex items-center gap-1.5 rounded-md bg-app2 px-2 py-1 text-xs">
          <StatusChip status={k} />
          <span className="font-mono text-ink">{n}</span>
        </span>
      ))}
    </div>
  );
}

function OverviewPanel() {
  const [ov, setOv] = useState<TenantOpsOverview | null>(null);
  const [readiness, setReadiness] = useState<ReadinessSummaryView | null>(null);
  const [readinessLoading, setReadinessLoading] = useState(true);
  const [readinessError, setReadinessError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    getAdminOverview().then(setOv).catch((e) => setError(describeError(e).message));
    // Readiness is admin-only. ONLY a 403 means "role-masked" (a reviewer) → show
    // it as unavailable. Any other failure (backend down, timeout, 500, bad body)
    // is a real error and must surface, not be hidden as role masking.
    getAdminReadiness()
      .then((r) => {
        setReadiness(r);
        setReadinessError(null);
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 403) {
          setReadiness(null); // reviewer role: summary not available
        } else {
          setReadinessError(describeError(e).message);
        }
      })
      .finally(() => setReadinessLoading(false));
  }, []);

  if (error) return <p className="text-xs text-danger">{error}</p>;
  if (!ov) return <p className="text-xs text-muted">Loading overview…</p>;

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card title="Packages by status">
        <CountRow counts={ov.package_counts_by_status} />
      </Card>
      <Card title="Jobs by status">
        <CountRow counts={ov.job_counts_by_status} />
      </Card>
      <Card title="Provider accounts">
        <p className="text-sm text-ink">
          <span className="font-mono">{ov.active_provider_accounts}</span> connected
        </p>
      </Card>
      <Card title="Deployment readiness">
        <div className="flex items-center gap-2">
          <StatusChip status={ov.degraded_readiness ? "degraded" : "ready"} />
          {readinessLoading ? (
            <span className="text-[11px] text-faint">checking…</span>
          ) : readinessError ? null : readiness ? (
            <span className="text-[11px] text-faint">{readiness.checks.length} checks</span>
          ) : (
            <span className="text-[11px] text-faint">summary not available for this role</span>
          )}
        </div>
        {readinessError ? (
          <p className="mt-2 text-xs text-danger">{readinessError}</p>
        ) : readiness ? (
          <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto">
            {readiness.checks.map((c) => (
              <li key={c.name} className="flex items-start justify-between gap-2 text-[11px]">
                <span className="text-muted">{c.name}</span>
                <StatusChip status={c.status} />
              </li>
            ))}
          </ul>
        ) : null}
      </Card>
    </div>
  );
}

function JobsPanel() {
  const [rows, setRows] = useState<JobAdminView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<JobAdminView | null>(null);

  const load = useCallback(() => {
    setError(null);
    listAdminJobs().then(setRows).catch((e) => setError(describeError(e).message));
  }, []);
  useEffect(() => load(), [load]);

  if (error) return <p className="text-xs text-danger">{error}</p>;
  if (rows === null) return <p className="text-xs text-muted">Loading jobs…</p>;
  if (rows.length === 0) return <p className="text-xs text-muted">No jobs in this tenant.</p>;

  return (
    <div className="flex h-full gap-4">
      <div className="w-1/2 overflow-y-auto">
        <table className="w-full text-left text-xs">
          <thead className="text-faint">
            <tr>
              <th className="py-1 pr-2 font-medium">Type</th>
              <th className="py-1 pr-2 font-medium">Status</th>
              <th className="py-1 font-medium">Created</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((j) => (
              <tr
                key={j.id}
                className={`cursor-pointer border-t border-line hover:bg-app2 ${open?.id === j.id ? "bg-app2" : ""}`}
                onClick={() => setOpen(j)}
              >
                <td className="py-1.5 pr-2 font-mono text-ink">{j.job_type}</td>
                <td className="py-1.5 pr-2"><StatusChip status={j.status} /></td>
                <td className="py-1.5 text-muted">{fmt(j.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="w-1/2 overflow-y-auto border-l border-line pl-4">
        {!open ? (
          <p className="text-xs text-faint">Select a job to see its safe status.</p>
        ) : (
          <div className="space-y-2 text-xs">
            <div className="flex items-center justify-between">
              <span className="font-mono text-ink">{open.job_type}</span>
              <StatusChip status={open.status} />
            </div>
            <div className="text-muted">attempt {open.attempt}/{open.max_attempts}</div>
            <div className="text-muted">created {fmt(open.created_at)} · started {fmt(open.started_at)} · finished {fmt(open.finished_at)}</div>
            {open.error_category ? (
              <div className="text-danger">error category: {open.error_category}</div>
            ) : null}
            {open.summary ? <div className="text-ink">{open.summary}</div> : null}
            <div>
              <div className="mb-1 text-faint">progress</div>
              <SafeMeta data={open.progress_safe} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function AuditPanel() {
  const [rows, setRows] = useState<AuditEventView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    listAdminAudit().then(setRows).catch((e) => setError(describeError(e).message));
  }, []);
  if (error) return <p className="text-xs text-danger">{error}</p>;
  if (rows === null) return <p className="text-xs text-muted">Loading audit log…</p>;
  if (rows.length === 0) return <p className="text-xs text-muted">No audit events in this tenant.</p>;
  return (
    <div className="h-full overflow-y-auto">
      <table className="w-full text-left text-xs">
        <thead className="text-faint">
          <tr>
            <th className="py-1 pr-2 font-medium">When</th>
            <th className="py-1 pr-2 font-medium">Action</th>
            <th className="py-1 pr-2 font-medium">Actor</th>
            <th className="py-1 pr-2 font-medium">Scope</th>
            <th className="py-1 font-medium">Msgs</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((e, i) => (
            <tr key={i} className="border-t border-line">
              <td className="py-1.5 pr-2 text-muted">{fmt(e.ts)}</td>
              <td className="py-1.5 pr-2 font-mono text-ink">{e.action}</td>
              <td className="py-1.5 pr-2 text-muted break-all">{e.actor}</td>
              <td className="py-1.5 pr-2 text-muted break-all">{e.scope ?? "—"}</td>
              <td className="py-1.5 text-muted">{e.message_count ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ExclusionsPanel() {
  const [data, setData] = useState<ExclusionSummaryView | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getAdminExclusionSummary().then(setData).catch((e) => setError(describeError(e).message));
  }, []);
  if (error) return <p className="text-xs text-danger">{error}</p>;
  if (!data) return <p className="text-xs text-muted">Loading exclusion summary…</p>;
  return (
    <div className="max-w-lg">
      <p className="mb-2 text-xs text-muted">
        Aggregate counts only — no excluded subjects, bodies, or message ids are ever shown.
        Total excluded: <span className="font-mono text-ink">{data.total_excluded}</span>
      </p>
      {data.by_type.length === 0 ? (
        <p className="text-xs text-faint">No exclusions recorded.</p>
      ) : (
        <table className="w-full text-left text-xs">
          <thead className="text-faint">
            <tr>
              <th className="py-1 pr-2 font-medium">Type</th>
              <th className="py-1 pr-2 font-medium">Category</th>
              <th className="py-1 font-medium">Count</th>
            </tr>
          </thead>
          <tbody>
            {data.by_type.map((r, i) => (
              <tr key={i} className="border-t border-line">
                <td className="py-1.5 pr-2 text-ink">{r.exclusion_type}</td>
                <td className="py-1.5 pr-2 text-muted">{r.aggregate_label || "—"}</td>
                <td className="py-1.5 font-mono text-ink">{r.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
