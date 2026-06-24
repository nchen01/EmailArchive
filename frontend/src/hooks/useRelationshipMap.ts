import { useEffect, useState } from "react";
import {
  ApiError,
  type ApiErrorKind,
  fetchRelationshipMap,
  type RelationshipMapQuery,
} from "../api/client";
import type { RelationshipMapResponse } from "../api/types";

interface UseRelationshipMapResult {
  data: RelationshipMapResponse | null;
  loading: boolean;
  error: string | null;
  errorKind: ApiErrorKind | null;
}

/**
 * Fetch the relationship map (S13) for the given mailbox + query. Clears stale
 * data at fetch start so a mailbox/mode switch never shows the previous result
 * (the S12-fix trust-boundary pattern). Re-runs when the serialized query
 * changes.
 */
export function useRelationshipMap(
  mailboxId: string | null,
  query: RelationshipMapQuery,
): UseRelationshipMapResult {
  const [data, setData] = useState<RelationshipMapResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [errorKind, setErrorKind] = useState<ApiErrorKind | null>(null);

  // Serialize the query so the effect re-runs on any field change without
  // depending on object identity.
  const key = JSON.stringify(query);

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
    setData(null);
    fetchRelationshipMap(mailboxId, query)
      .then((res) => {
        if (!cancelled) setData(res);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mailboxId, key]);

  return { data, loading, error, errorKind };
}
