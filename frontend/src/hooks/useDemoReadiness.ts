import { useCallback, useEffect, useState } from "react";
import { fetchPreflight } from "../api/client";
import type { PreflightCheck } from "../api/types";

interface UseDemoReadinessResult {
  /** Preflight checks keyed by name, or null until the first load resolves. */
  checks: Record<string, PreflightCheck> | null;
  loading: boolean;
  /** True if the preflight call itself failed (backend down). Non-fatal. */
  failed: boolean;
  /** Re-run the preflight check (e.g. a Status-screen refresh button). */
  reload: () => void;
}

/**
 * Fetch operational readiness for the demo status strip (S11). This is purely
 * advisory: it never blocks the app, and a failed preflight call just leaves the
 * strip in an "unknown" state. Re-runs when the mailbox changes.
 */
export function useDemoReadiness(
  mailboxId: string | null,
): UseDemoReadinessResult {
  const [checks, setChecks] = useState<Record<string, PreflightCheck> | null>(
    null,
  );
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!mailboxId) {
      setChecks(null);
      setFailed(false);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setFailed(false);
    fetchPreflight(mailboxId)
      .then((res) => {
        if (cancelled) return;
        const byName: Record<string, PreflightCheck> = {};
        for (const c of res.checks) byName[c.name] = c;
        setChecks(byName);
      })
      .catch(() => {
        if (!cancelled) {
          setChecks(null);
          setFailed(true);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [mailboxId, nonce]);

  return { checks, loading, failed, reload };
}
