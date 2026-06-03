import type { ContactDetail, NetworkMapData } from "./types";

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
