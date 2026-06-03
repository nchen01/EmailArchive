import { useEffect, useState } from "react";
import { fetchContactDetail } from "../api/client";
import type { ContactDetail } from "../api/types";

interface UseContactDetailResult {
  detail: ContactDetail | null;
  loading: boolean;
  error: string | null;
}

export function useContactDetail(
  mailboxId: string | null,
  personId: string | null,
): UseContactDetailResult {
  const [detail, setDetail] = useState<ContactDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!mailboxId || !personId) {
      setDetail(null);
      setError(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setDetail(null);
    fetchContactDetail(mailboxId, personId)
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
  }, [mailboxId, personId]);

  return { detail, loading, error };
}
