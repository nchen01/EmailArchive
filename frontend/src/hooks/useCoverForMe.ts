import { useCallback, useState } from "react";
import {
  ApiError,
  type ApiErrorKind,
  fetchCoverForMe,
  SummariesNotConfiguredError,
} from "../api/client";
import type { CoverForMeResponse } from "../api/types";

interface UseCoverForMeResult {
  response: CoverForMeResponse | null;
  loading: boolean;
  /** Generic transport/server error (not the 503 "not configured" case). */
  error: string | null;
  /** Classified error kind for a distinct, human-readable title. */
  errorKind: ApiErrorKind | null;
  /** True when the API key is absent (HTTP 503). Rendered distinctly. */
  notConfigured: boolean;
  /** Fire a query explicitly (called from the "Ask" button, never on keystroke). */
  ask: (query: string) => Promise<void>;
  /** Clear any previous answer/error (e.g. when the mailbox changes). */
  reset: () => void;
}

/**
 * Imperative cover-for-me query hook (S5). The request is sent only when ``ask``
 * is called — there is no effect that fires on input change, so typing in the
 * box does not hit the API.
 */
export function useCoverForMe(mailboxId: string | null): UseCoverForMeResult {
  const [response, setResponse] = useState<CoverForMeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [errorKind, setErrorKind] = useState<ApiErrorKind | null>(null);
  const [notConfigured, setNotConfigured] = useState(false);

  const reset = useCallback(() => {
    setResponse(null);
    setError(null);
    setErrorKind(null);
    setNotConfigured(false);
    setLoading(false);
  }, []);

  const ask = useCallback(
    async (query: string) => {
      const trimmed = query.trim();
      if (!mailboxId || !trimmed) return;
      setLoading(true);
      setError(null);
      setErrorKind(null);
      setNotConfigured(false);
      setResponse(null);
      try {
        const res = await fetchCoverForMe(mailboxId, trimmed);
        setResponse(res);
      } catch (err) {
        if (err instanceof SummariesNotConfiguredError) {
          setNotConfigured(true);
        } else {
          setError(err instanceof Error ? err.message : String(err));
          setErrorKind(err instanceof ApiError ? err.kind : null);
        }
      } finally {
        setLoading(false);
      }
    },
    [mailboxId],
  );

  return { response, loading, error, errorKind, notConfigured, ask, reset };
}
