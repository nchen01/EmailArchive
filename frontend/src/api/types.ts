// TypeScript types matching services/api/schemas/network_map.py exactly.

export interface Owner {
  person_id: string;
  name: string;
  canonical_email: string;
}

export interface Node {
  person_id: string;
  name: string;
  canonical_email: string;
  role: string;
  role_confidence: number;
  org_domain: string | null;
  weight: number;
  message_count: number;
  last_contact: string; // ISO datetime string
}

export interface Edge {
  person_id: string;
  weight: number;
  message_count: number;
  sent_to_count: number;
  received_count: number;
  first_contact: string;
  last_contact: string;
}

export interface NetworkMapData {
  owner: Owner;
  nodes: Node[];
  edges: Edge[];
}

export interface ThreadSummary {
  thread_id: string;
  subject: string;
  other_participants: string[];
  last: string;
  message_count: number;
}

export interface PersonDetail {
  person_id: string;
  name: string;
  canonical_email: string;
  all_emails: string[];
  role: string;
  role_confidence: number;
  org_domain: string | null;
}

export interface ContactDetail {
  person: PersonDetail;
  edge: Edge;
  recent_threads: ThreadSummary[];
}

// ── Project view (spec 02 §5) ────────────────────────────────────────────────

export interface ProjectSummary {
  id: string;
  label: string;
  state: string; // "active" | "stale"
  confidence: number;
  start: string;
  end: string;
  member_count: number;
  thread_count: number;
}

export interface ProjectListData {
  projects: ProjectSummary[];
}

export interface ProjectMetrics {
  members: number;
  threads: number;
  last_activity: string;
}

export interface WhoToAsk {
  person_id: string;
  name: string;
  role: string;
  in_project_count: number;
  weight: number;
}

export interface ProjectMember {
  person_id: string;
  name: string;
  role: string;
  in_project_count: number;
  involvement: number;
}

export interface RecentThread {
  thread_id: string;
  subject: string;
  participants: string[];
  last: string;
}

export interface ActivityItem {
  type: string; // "proposed" | "did" | "outcome"
  summary: string;
  actor_person_id: string;
  source_message_ids: string[];
  confidence: number;
}

export interface ProjectDetailData {
  id: string;
  label: string;
  state: string;
  confidence: number;
  start: string;
  end: string;
  metrics: ProjectMetrics;
  who_to_ask: WhoToAsk[];
  members: ProjectMember[];
  recent_threads: RecentThread[];
  activity: ActivityItem[]; // S4 — Events from the event table
}

// ── L3 synthesis (spec 02 §6, spec 05 §3.4) ──────────────────────────────────

export interface SynthesisClaim {
  text: string;
  source_message_ids: string[];
}

export interface SynthesisResult {
  claims: SynthesisClaim[];
  model: string;
  usage: Record<string, number>;
  state: string | null;
}

// ── Cover-for-me query (S5/S8, D11) ──────────────────────────────────────────

export interface CoverForMeRequest {
  query: string;
}

/** Metadata for one message cited in result.claims. Never contains uncited hits. */
export interface EvidenceMessage {
  message_id_header: string;
  subject: string;
  date: string; // ISO 8601
  snippet: string; // first 200 chars of clean_text
}

/** Structured operational state for L2 retrieval (S8.4). */
export type RetrievalStatus =
  | "active"               // L2 ran and returned cited hits
  | "active_l1_only"       // L2 ran (or was skipped for pure-L1 path) but L1 answered
  | "disabled_no_key"      // VOYAGE_API_KEY absent — expected in dev
  | "degraded_rate_limit"  // Voyage rate-limit hit this request
  | "no_embeddings"        // No embeddings found for this mailbox
  | "unavailable";         // Voyage client or request failed

export interface CoverForMeResponse {
  query: string;
  routed_to: string | null; // "person:<name>" | "project:<label>" | null
  result: SynthesisResult;
  /** S8.2: one entry per unique header cited across result.claims. */
  supporting_evidence: EvidenceMessage[];
  /** S8.4: structured operational state; defaults to "active". */
  retrieval_status: RetrievalStatus;
}

// ── Preflight (S8.3, consumed by the S11 demo-readiness strip) ───────────────

/** One operational check. Matches services/api/routers/preflight.py CheckOut. */
export interface PreflightCheck {
  name: string;
  status: "pass" | "fail" | "warn" | "info";
  message: string;
}

export interface PreflightResponse {
  ok: boolean;
  checks: PreflightCheck[];
}
