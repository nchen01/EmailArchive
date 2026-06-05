import { useEffect, useState } from "react";
import { fetchProjectDetail } from "../api/client";
import type { ProjectDetailData } from "../api/types";

interface UseProjectDetailResult {
  detail: ProjectDetailData | null;
  loading: boolean;
  error: string | null;
}

export function useProjectDetail(
  mailboxId: string | null,
  projectId: string | null,
): UseProjectDetailResult {
  const [detail, setDetail] = useState<ProjectDetailData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!mailboxId || !projectId) {
      setDetail(null);
      setError(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setDetail(null);
    fetchProjectDetail(mailboxId, projectId)
      .then((result) => {
        if (!cancelled) setDetail(result);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setDetail(null);
          setError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [mailboxId, projectId]);

  return { detail, loading, error };
}
