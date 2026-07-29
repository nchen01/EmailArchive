import type {
  ContactDetail,
  CoverForMeResponse,
  GmailIngestRequest,
  GmailWindowRequest,
  GmailWindowResponse,
  HandoffPackage,
  NetworkMapData,
  PreflightResponse,
  ScopeRequestBody,
  ProjectDetailData,
  ProjectListData,
  PublishRequest,
  PublishResponse,
  RecipientAskResponse,
  RecipientPackage,
  RecipientSession,
  RelationshipMapMode,
  RelationshipMapResponse,
  RelationshipType,
  SourceMessageDetail,
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

async function sendJsonBody<T>(method: string, url: string, body: unknown): Promise<T> {
  const res = await fetchWithTimeout(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
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
 * Fetch citation-safe detail for one source message (S14). Keyed on the RFC
 * message_id_header (the citation key). Returns ApiError kind "not_found" (404)
 * when the header is missing, in a different mailbox, or sensitivity-excluded —
 * callers should treat all three the same ("detail not available"), never
 * inferring existence of a sensitive message.
 */
export async function fetchSourceMessage(
  mailboxId: string,
  messageIdHeader: string,
): Promise<SourceMessageDetail> {
  const url = `${API_BASE}/api/source-message/${encodeURIComponent(
    mailboxId,
  )}?message_id_header=${encodeURIComponent(messageIdHeader)}`;
  return getJson<SourceMessageDetail>(url);
}

export interface RelationshipMapQuery {
  mode: RelationshipMapMode;
  rootId?: string | null;
  projectId?: string | null;
  minWeight?: number;
  recencyDays?: number | null;
  relationshipTypes?: RelationshipType[];
}

/** Relationship map (S13). Derived live by the backend from existing tables. */
export async function fetchRelationshipMap(
  mailboxId: string,
  q: RelationshipMapQuery,
): Promise<RelationshipMapResponse> {
  const params = new URLSearchParams();
  params.set("mode", q.mode);
  if (q.rootId) params.set("root_id", q.rootId);
  if (q.projectId) params.set("project_id", q.projectId);
  if (q.minWeight && q.minWeight > 0) params.set("min_weight", String(q.minWeight));
  if (q.recencyDays != null) params.set("recency_days", String(q.recencyDays));
  for (const t of q.relationshipTypes ?? []) params.append("relationship_types", t);
  const url = `${API_BASE}/api/relationship-map/${encodeURIComponent(
    mailboxId,
  )}?${params.toString()}`;
  return getJson<RelationshipMapResponse>(url);
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

// ── Handoff package (creator draft/scope/generate; S17.3 backend, S17.4 UI) ───

/** Create a draft handoff package for the covered employee's own mailbox. */
export async function createHandoff(
  mailboxId: string,
  body: { reason: string; title?: string },
): Promise<HandoffPackage> {
  return postJsonBody<HandoffPackage>(
    `${API_BASE}/api/handoff/${encodeURIComponent(mailboxId)}`,
    body,
  );
}

/**
 * Replace the package scope (draft/generated only). The scope PATCH is
 * REPLACE-LIKE: pass the COMPLETE current scope — omitted arrays reset to empty.
 */
export async function updateHandoffScope(
  packageId: string,
  scope: ScopeRequestBody,
): Promise<HandoffPackage> {
  return sendJsonBody<HandoffPackage>(
    "PATCH",
    `${API_BASE}/api/handoff/${encodeURIComponent(packageId)}/scope`,
    scope,
  );
}

/** (Re)generate the candidate package from the current scope. */
export async function generateHandoff(packageId: string): Promise<HandoffPackage> {
  return postJsonBody<HandoffPackage>(
    `${API_BASE}/api/handoff/${encodeURIComponent(packageId)}/generate`,
    {},
  );
}

/** Fetch the creator review view of a package. */
export async function getHandoff(packageId: string): Promise<HandoffPackage> {
  return getJson<HandoffPackage>(
    `${API_BASE}/api/handoff/${encodeURIComponent(packageId)}`,
  );
}

/**
 * Publish a generated package to its single recipient (S17.5). Freezes the
 * package and returns the ONE-TIME capability code + share fragment. The caller
 * must treat `capability_code`/`share_fragment` as transient (show once, never
 * store, never log) — the server keeps only a hash and cannot re-issue them.
 */
export async function publishHandoff(
  packageId: string,
  body: PublishRequest,
): Promise<PublishResponse> {
  return postJsonBody<PublishResponse>(
    `${API_BASE}/api/handoff/${encodeURIComponent(packageId)}/publish`,
    body,
  );
}

/**
 * Revoke a published package (S17.5): blocks the recipient's access immediately
 * and kills any live session. Returns the updated package (status "revoked").
 */
export async function revokeHandoff(packageId: string): Promise<HandoffPackage> {
  return postJson<HandoffPackage>(
    `${API_BASE}/api/handoff/${encodeURIComponent(packageId)}/revoke`,
  );
}

/**
 * Fork a frozen package (published/revoked/superseded) into a fresh DRAFT in the
 * same lineage (S17.10). The recovery path when the share link was lost, the
 * recipient was wrong, or the scope needs revising — published packages are
 * immutable, so the creator makes a new version instead. Returns the new draft
 * (copied scope, no claims/evidence/recipient/code); Generate + publish it to
 * mint a fresh one-time link, which supersedes the prior published version.
 */
export async function newVersionHandoff(packageId: string): Promise<HandoffPackage> {
  return postJson<HandoffPackage>(
    `${API_BASE}/api/handoff/${encodeURIComponent(packageId)}/new-version`,
  );
}

/**
 * URL of the creator-side static HTML export for a frozen package (S17.11). It is
 * a plain GET the browser can download (the server sets Content-Disposition:
 * attachment); the returned document is self-contained and read-only.
 */
export function handoffExportUrl(packageId: string): string {
  return `${API_BASE}/api/handoff/${encodeURIComponent(packageId)}/export.html`;
}

// ── Handoff package — recipient view (S17.5 backend, S17.6 UI) ────────────────

/**
 * Exchange a one-time capability code (read from the share-link URL fragment) for
 * a short-lived recipient session.
 *
 * The raw code travels ONLY in the POST body — never a URL path/query — and this
 * function does not log it. The code is one-time (consumed on first exchange), so
 * any invalid/expired/revoked/already-consumed code yields HTTP 403; the caller
 * renders every such failure as the same neutral "unavailable" state (no oracle
 * about why). The returned session token is short-lived and must be held in
 * memory only, never persisted.
 */
export async function startRecipientSession(code: string): Promise<RecipientSession> {
  const res = await fetchWithTimeout(`${API_BASE}/api/handoff/recipient/session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  if (!res.ok) {
    throw await toHttpError(res);
  }
  return (await res.json()) as RecipientSession;
}

/**
 * Read the published, package-local recipient view for a live session. The
 * session token is sent as an `Authorization: Bearer` header (never in the URL).
 *
 * The response is package-scoped by construction: claims + evidence + a constant
 * privacy posture only — no mailbox id, no exclusion counts, no Gmail/source
 * link. A 403 (session expired, or package revoked/expired/superseded) is again
 * rendered as the neutral "unavailable" state.
 */
export async function getRecipientPackage(sessionToken: string): Promise<RecipientPackage> {
  const res = await fetchWithTimeout(`${API_BASE}/api/handoff/recipient/package`, {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  if (!res.ok) {
    throw await toHttpError(res);
  }
  return (await res.json()) as RecipientPackage;
}

/**
 * Ask a question against ONLY the recipient's package (S17.9). The query is sent
 * in the POST body and the session token as a Bearer header (never the URL). The
 * answer is deterministic and package-local: cited evidence is in-package only,
 * and `answered: false` is the neutral no-evidence result (no existence oracle).
 */
export async function askRecipientPackage(
  sessionToken: string,
  query: string,
): Promise<RecipientAskResponse> {
  const res = await fetchWithTimeout(`${API_BASE}/api/handoff/recipient/ask`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${sessionToken}`,
    },
    body: JSON.stringify({ query }),
  });
  if (!res.ok) {
    throw await toHttpError(res);
  }
  return (await res.json()) as RecipientAskResponse;
}

/**
 * Preview a date-windowed Gmail ingest (S16.0): counts matching messages without
 * fetching bodies or persisting. The browser never sends an OAuth token — the
 * backend reads it from its own environment.
 */
export async function previewGmailWindow(
  mailboxId: string,
  req: GmailWindowRequest,
): Promise<GmailWindowResponse> {
  const url = `${API_BASE}/api/gmail-ingest/${encodeURIComponent(mailboxId)}/preview`;
  return postJsonBody<GmailWindowResponse>(url, req);
}

/** S25: a date-windowed Gmail ingest now enqueues a background job. */
export interface GmailIngestJobRef {
  job_id: string;
  status: string;
  mode: string;
}

/** Safe job view (S24) — no tokens/content, only status + safe progress. */
export interface JobView {
  id: string;
  status: string;
  progress: Record<string, unknown>;
  summary: string | null;
  error_category: string | null;
}

const JOB_TERMINAL = new Set(["succeeded", "failed", "canceled", "partially_succeeded"]);
export function isJobTerminal(status: string): boolean {
  return JOB_TERMINAL.has(status);
}

/**
 * Start a date-windowed Gmail ingest (S16.0 safeguards preserved; S25). Validation
 * + account verify happen request-time; the fetch/persist runs in a
 * `gmail_ingest_window` job. Poll `getJob(job_id)` for status/progress.
 */
export async function ingestGmailWindow(
  mailboxId: string,
  req: GmailIngestRequest,
): Promise<GmailIngestJobRef> {
  const url = `${API_BASE}/api/gmail-ingest/${encodeURIComponent(mailboxId)}/ingest`;
  return postJsonBody<GmailIngestJobRef>(url, req);
}

export async function getJob(jobId: string): Promise<JobView> {
  return getJson<JobView>(`${API_BASE}/api/jobs/${encodeURIComponent(jobId)}`);
}

// ── Gmail OAuth connect (S23) ────────────────────────────────────────────────
// Safe metadata only — no tokens ever cross this boundary.
export interface GmailConnectionStatus {
  connected: boolean;
  provider_account_email?: string | null;
  scopes_granted?: string[];
  status?: string | null;
  connected_at?: string | null;
}

export async function getGmailStatus(mailboxId: string): Promise<GmailConnectionStatus> {
  return getJson<GmailConnectionStatus>(
    `${API_BASE}/api/mailbox/${encodeURIComponent(mailboxId)}/gmail/status`,
  );
}

/** Start Gmail OAuth; returns the Google authorization URL to redirect the browser to. */
export async function startGmailConnect(mailboxId: string): Promise<{ authorization_url: string }> {
  return postJson<{ authorization_url: string }>(
    `${API_BASE}/api/mailbox/${encodeURIComponent(mailboxId)}/gmail/connect/start`,
  );
}

export async function disconnectGmail(mailboxId: string): Promise<{ disconnected: boolean }> {
  return postJson<{ disconnected: boolean }>(
    `${API_BASE}/api/mailbox/${encodeURIComponent(mailboxId)}/gmail/disconnect`,
  );
}
