import type { PreflightCheck } from "../api/types";
import {
  computeReadinessItems,
  INDICATOR_DOT_CLASS,
} from "../utils/readiness";

interface DemoReadinessStripProps {
  /** Preflight checks keyed by name, or null until loaded. */
  checks: Record<string, PreflightCheck> | null;
  loading: boolean;
  /** True if the preflight call itself failed (backend down). */
  failed: boolean;
  /** Loaded contact count (network map), or null while unknown/loading. */
  contactCount: number | null;
  /** Loaded project count, or null while unknown/loading. */
  projectCount: number | null;
}

/**
 * Unobtrusive status strip (S11/S12) showing whether the loaded mailbox is
 * demo-ready: contacts, projects, embeddings, retrieval, and synthesis.
 *
 * Presentational only — readiness data is supplied by the caller (Workspace
 * fetches /api/preflight once and shares it), so this never triggers its own
 * request and never blocks the app. While preflight is loading or has failed,
 * dependent dots read "unknown".
 */
export function DemoReadinessStrip({
  checks,
  loading,
  failed,
  contactCount,
  projectCount,
}: DemoReadinessStripProps) {
  const items = computeReadinessItems({
    checks,
    failed,
    contactCount,
    projectCount,
  });

  return (
    <div className="readiness-strip" role="status" aria-label="Demo readiness">
      <span className="readiness-title">
        Readiness{loading ? " · checking…" : ""}
      </span>
      {items.map((it) => (
        <span key={it.key} className="readiness-item" title={it.hint}>
          <span className={INDICATOR_DOT_CLASS[it.indicator]} aria-hidden="true" />
          {it.label}
        </span>
      ))}
      {failed ? (
        <span className="readiness-item readiness-muted" title="Preflight unavailable">
          (backend status unavailable)
        </span>
      ) : null}
    </div>
  );
}
