import { useCallback, useEffect, useState } from "react";
import { ApiError, type ApiErrorKind, fetchNetworkMap } from "../api/client";
import type { NetworkMapData } from "../api/types";

interface UseNetworkMapResult {
  data: NetworkMapData | null;
  loading: boolean;
  error: string | null;
  errorKind: ApiErrorKind | null;
  reload: () => void;
}

export function useNetworkMap(mailboxId: string | null): UseNetworkMapResult {
  const [data, setData] = useState<NetworkMapData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [errorKind, setErrorKind] = useState<ApiErrorKind | null>(null);
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!mailboxId) {
      setData(null);
      setError(null);
      setErrorKind(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setErrorKind(null);
    // Clear the previous mailbox's graph immediately so a switch never shows
    // mailbox A's data (owner/contacts) while mailbox B is still loading.
    setData(null);
    // Fetch the full graph; role filtering is applied client-side so toggling
    // legend chips does not require a round-trip.
    fetchNetworkMap(mailboxId)
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setData(null);
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

  return { data, loading, error, errorKind, reload };
}
