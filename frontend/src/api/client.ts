import type {
  ContactDetail,
  CoverForMeResponse,
  NetworkMapData,
  PreflightResponse,
  ProjectDetailData,
  ProjectListData,
  SynthesisResult,
} from "./types";

/**
 * Thrown when synthesis is unavailable because the Anthropic key is not
 * configured (HTTP 503). The UI shows a "Summaries not configured" message.
 */
export class SummariesNotConfiguredError extends Error {
  constructor(message = "Summaries are not configured") {
    super(message);
    this.name = "SummariesNotConfiguredError";
  }
}

/**
 * Distinguishable API error kinds so views can render an accurate state instead
 * of one generic "failed" message (or, worse, spinning forever):
 *  - "unreachable": the request never reached the backend (proxy down, DNS,
 *    connection refused) — fetch itself rejected with a TypeError.
 *  - "timeout": the request was aborted after REQUEST_TIMEOUT_MS — a hung
 *    backend (e.g. blocked Voyage import, stuck preflight) must not hang the UI.
 *  - "not_found": HTTP 404 — typically a wrong/unknown mailbox id.
 *  - "http": any other non-2xx response.
 */
export type ApiErrorKind = "unreachable" | "timeout" | "not_found" | "http";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status?: number;

  constructor(kind: ApiErrorKind, message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
  }
}

// Empty string in dev: requests hit Vite, which proxies /api -> FastAPI.
// Override with VITE_API_URL for non-proxied deployments.
const API_BASE = import.meta.env.VITE_API_URL ?? "";

// Hard cap on every request. Without this a hung backend leaves the UI stuck
// on "Loading…" indefinitely (the exact operator-facing failure we are fixing).
const REQUEST_TIMEOUT_MS = 15000;

/** fetch() with an AbortController timeout. Maps low-level failures to ApiError. */
async function fetchWithTimeout(url: string, init?: RequestInit): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError(
        "timeout",
        `Request timed out after ${REQUEST_TIMEOUT_MS / 1000}s — the backend may be ` +
          `unresponsive. Check the backend window (scripts\\run_backend.ps1).`,
      );
    }
    // fetch() rejects with a TypeError when it cannot reach the server at all
    // (connection refused, proxy down, DNS). Surface that distinctly.
    throw new ApiError(
      "unreachable",
      "Cannot reach the backend API. Is it running? Start it with " +
        "scripts\\run_backend.ps1 (expected at http://localhost:8000).",
    );
  } finally {
    clearTimeout(timer);
  }
}

/** Turn a non-2xx Response into the right ApiError, reading detail when present. */
async function toHttpError(res: Response): Promise<ApiError> {
  let detail = "";
  try {
    const body = await res.json();
    detail = body?.detail ? `: ${body.detail}` : "";
  } catch {
    // non-JSON error body; ignore
  }
  if (res.status === 404) {
    return new ApiError(
      "not_found",
      `Not found (404)${detail} — check the mailbox ID.`,
      404,
    );
  }
  return new ApiError("http", `Request failed (${res.status})${detail}`, res.status);
}

/** Lightweight backend reachability probe. Resolves true iff /api/health is OK. */
export async function checkBackendHealth(): Promise<boolean> {
  try {
    const res = await fetchWithTimeout(`${API_BASE}/api/health`);
    return res.ok;
  } catch {
    return false;
  }
}

/**
 * Map any thrown error into a short title + human message so views can render a
 * distinct, non-technical state. Backend-unreachable, timeout, not-found, and
 * generic HTTP failures each get their own title. Anything that is not an
 * ApiError (should be rare) falls back to a generic "Something went wrong".
 */
/** Short, human-readable heading for a classified error kind (for ErrorBanner). */
export function errorKindTitle(kind: ApiErrorKind | null): string {
  switch (kind) {
    case "unreachable":
      return "Backend unavailable";
    case "timeout":
      return "Request timed out";
    case "not_found":
      return "Not found";
    default:
      return "Couldn't load this view";
  }
}

export function describeError(err: unknown): { title: string; message: string } {
  if (err instanceof ApiError) {
    switch (err.kind) {
      case "unreachable":
        return { title: "Backend unavailable", message: err.message };
      case "timeout":
        return { title: "Request timed out", message: err.message };
      case "not_found":
        return { title: "Not found", message: err.message };
      default:
        return { title: "Request failed", message: err.message };
    }
  }
  return {
    title: "Something went wrong",
    message: err instanceof Error ? err.message : String(err),
  };
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetchWithTimeout(url);
  if (!res.ok) {
    throw await toHttpError(res);
  }
  return (await res.json()) as T;
}

async function postJson<T>(url: string): Promise<T> {
  const res = await fetchWithTimeout(url, { method: "POST" });
  if (!res.ok) {
    if (res.status === 503) {
      throw new SummariesNotConfiguredError();
    }
    throw await toHttpError(res);
  }
  return (await res.json()) as T;
}

async function postJsonBody<T>(url: string, body: unknown): Promise<T> {
  const res = await fetchWithTimeout(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    if (res.status === 503) {
      throw new SummariesNotConfiguredError();
    }
    throw await toHttpError(res);
  }
  return (await res.json()) as T;
}

export async function fetchNetworkMap(
  mailboxId: string,
  roles?: string[],
  minWeight = 0,
): Promise<NetworkMapData> {
  const params = new URLSearchParams();
  if (roles && roles.length > 0) {
    params.set("roles", roles.join(","));
  }
  if (minWeight > 0) {
    params.set("min_weight", String(minWeight));
  }
  const qs = params.toString();
  const url = `${API_BASE}/api/network-map/${encodeURIComponent(mailboxId)}${
    qs ? `?${qs}` : ""
  }`;
  return getJson<NetworkMapData>(url);
}

export async function fetchContactDetail(
  mailboxId: string,
  personId: string,
): Promise<ContactDetail> {
  const url = `${API_BASE}/api/network-map/${encodeURIComponent(
    mailboxId,
  )}/contact/${encodeURIComponent(personId)}`;
  return getJson<ContactDetail>(url);
}

export async function fetchProjects(
  mailboxId: string,
): Promise<ProjectListData> {
  const url = `${API_BASE}/api/projects/${encodeURIComponent(mailboxId)}`;
  return getJson<ProjectListData>(url);
}

export async function fetchProjectDetail(
  mailboxId: string,
  projectId: string,
): Promise<ProjectDetailData> {
  const url = `${API_BASE}/api/projects/${encodeURIComponent(
    mailboxId,
  )}/${encodeURIComponent(projectId)}`;
  return getJson<ProjectDetailData>(url);
}

/** Trigger "What's been done" L3 synthesis for a project (spec 02 §6). */
export async function fetchProjectSummary(
  mailboxId: string,
  projectId: string,
): Promise<SynthesisResult> {
  const url = `${API_BASE}/api/synthesis/${encodeURIComponent(
    mailboxId,
  )}/project/${encodeURIComponent(projectId)}`;
  return postJson<SynthesisResult>(url);
}

/** Trigger "Ask about this contact" L3 synthesis (spec 05 §3.4). */
export async function fetchContactSummary(
  mailboxId: string,
  personId: string,
): Promise<SynthesisResult> {
  const url = `${API_BASE}/api/synthesis/${encodeURIComponent(
    mailboxId,
  )}/contact/${encodeURIComponent(personId)}`;
  return postJson<SynthesisResult>(url);
}

/**
 * Cover-for-me query (S5, D11): a bounded natural-language question routed over
 * structured L1 data, answered with a cited SynthesisResult.
 */
export async function fetchCoverForMe(
  mailboxId: string,
  query: string,
): Promise<CoverForMeResponse> {
  const url = `${API_BASE}/api/cover-for-me/${encodeURIComponent(mailboxId)}`;
  return postJsonBody<CoverForMeResponse>(url, { query });
}

/**
 * Operational preflight for the demo-readiness strip (S8.3 endpoint). Passing a
 * mailbox id includes the per-mailbox embeddings check. Never throws on a failed
 * check — the endpoint returns ok:false with details; only transport failures
 * reject (the strip treats those as "unknown").
 */
export async function fetchPreflight(
  mailboxId?: string,
): Promise<PreflightResponse> {
  const qs = mailboxId ? `?mailbox_id=${encodeURIComponent(mailboxId)}` : "";
  return getJson<PreflightResponse>(`${API_BASE}/api/preflight${qs}`);
}
