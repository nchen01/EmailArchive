import { useCallback, useEffect, useState } from "react";
import { ApiError, type ApiErrorKind, fetchProjects } from "../api/client";
import type { ProjectSummary } from "../api/types";

interface UseProjectsResult {
  projects: ProjectSummary[];
  loading: boolean;
  error: string | null;
  errorKind: ApiErrorKind | null;
  reload: () => void;
}

export function useProjects(mailboxId: string | null): UseProjectsResult {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [errorKind, setErrorKind] = useState<ApiErrorKind | null>(null);
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!mailboxId) {
      setProjects([]);
      setError(null);
      setErrorKind(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setErrorKind(null);
    // Clear the previous mailbox's projects immediately so a switch never shows
    // mailbox A's project list/counts/suggestions while mailbox B is loading.
    setProjects([]);
    fetchProjects(mailboxId)
      .then((result) => {
        if (!cancelled) setProjects(result.projects);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setProjects([]);
          setError(err instanceof Error ? err.message : String(err));
          setErrorKind(err instanceof ApiError ? err.kind : null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [mailboxId, nonce]);

  return { projects, loading, error, errorKind, reload };
}
