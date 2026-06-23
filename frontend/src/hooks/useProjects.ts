import { useCallback, useEffect, useState } from "react";
import { fetchProjects } from "../api/client";
import type { ProjectSummary } from "../api/types";

interface UseProjectsResult {
  projects: ProjectSummary[];
  loading: boolean;
  error: string | null;
  reload: () => void;
}

export function useProjects(mailboxId: string | null): UseProjectsResult {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!mailboxId) {
      setProjects([]);
      setError(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchProjects(mailboxId)
      .then((result) => {
        if (!cancelled) setProjects(result.projects);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setProjects([]);
          setError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [mailboxId, nonce]);

  return { projects, loading, error, reload };
}
