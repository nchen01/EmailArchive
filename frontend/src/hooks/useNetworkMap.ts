import { useCallback, useEffect, useState } from "react";
import { fetchNetworkMap } from "../api/client";
import type { NetworkMapData } from "../api/types";

interface UseNetworkMapResult {
  data: NetworkMapData | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

export function useNetworkMap(mailboxId: string | null): UseNetworkMapResult {
  const [data, setData] = useState<NetworkMapData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!mailboxId) {
      setData(null);
      setError(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
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
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [mailboxId, nonce]);

  return { data, loading, error, reload };
}
