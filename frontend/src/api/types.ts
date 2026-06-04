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
  activity: unknown[]; // @sprint S4 — always empty in S3
}
