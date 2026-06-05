import type {
  ContactDetail,
  NetworkMapData,
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

// Empty string in dev: requests hit Vite, which proxies /api -> FastAPI.
// Override with VITE_API_URL for non-proxied deployments.
const API_BASE = import.meta.env.VITE_API_URL ?? "";

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = body?.detail ? `: ${body.detail}` : "";
    } catch {
      // non-JSON error body; ignore
    }
    throw new Error(`Request failed (${res.status})${detail}`);
  }
  return (await res.json()) as T;
}

async function postJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { method: "POST" });
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = body?.detail ? `: ${body.detail}` : "";
    } catch {
      // non-JSON error body; ignore
    }
    if (res.status === 503) {
      throw new SummariesNotConfiguredError();
    }
    throw new Error(`Request failed (${res.status})${detail}`);
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
