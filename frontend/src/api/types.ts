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

/** Which layer surfaced a citation. */
export type EvidenceSourceType = "l1_structured" | "l2_retrieval";

/** Metadata for one message cited in result.claims. Never contains uncited hits. */
export interface EvidenceMessage {
  message_id_header: string;
  subject: string;
  date: string; // ISO 8601
  snippet: string; // first 200 chars of clean_text
  // S14 — optional (older responses omit them; treat as defaults).
  sender_display?: string;
  sender_domain?: string;
  source_type?: EvidenceSourceType | null;
  /** Best-effort Gmail rfc822msgid search link; Gmail mailboxes only, else null. */
  open_url?: string | null;
}

/**
 * Full detail for one cited source message (S14), from
 * GET /api/source-message/{mailbox_id}?message_id_header=...
 * Only citation-safe metadata; never body/MIME/tokens; sensitive messages 404.
 */
export interface SourceMessageDetail {
  message_id_header: string;
  subject: string;
  date: string; // ISO 8601 ("" if unknown)
  sender_display: string;
  sender_domain: string;
  provider_type: "gmail" | "msgraph";
  snippet: string;
  source_type?: EvidenceSourceType | null;
  open_url?: string | null;
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

// ── Relationship map (S13) ───────────────────────────────────────────────────

export type RelationshipNodeType =
  | "owner"
  | "person"
  | "project"
  | "organization"
  | "thread_group";

export type RelationshipType =
  | "direct_exchange"
  | "thread_copresence"
  | "project_copresence"
  | "org_affiliation"
  | "bridge";

export type RelationshipMapMode = "owner" | "project" | "org" | "graph";

export interface RelationshipNode {
  id: string;
  node_type: RelationshipNodeType;
  label: string;
  subtitle: string | null;
  role: string | null;
  confidence: number | null;
  metadata: Record<string, unknown>;
}

export interface RelationshipEdge {
  id: string;
  source_id: string;
  target_id: string;
  relationship_type: RelationshipType;
  evidence_kind: "message_headers" | "thread_ids" | "project_ids" | "domain";
  weight: number;
  confidence: number | null;
  evidence_count: number;
  source_message_ids: string[];
  thread_ids: string[];
  project_ids: string[];
  first_seen: string | null;
  last_seen: string | null;
  muted: boolean;
  explanation: string;
}

export interface RelationshipGroup {
  id: string;
  label: string;
  node_ids: string[];
}

export interface RelationshipMapResponse {
  root: RelationshipNode | null;
  nodes: RelationshipNode[];
  edges: RelationshipEdge[];
  groups: RelationshipGroup[];
  layout_hint: "tree" | "graph";
  mode: RelationshipMapMode;
  generated_from: {
    threads: number;
    projects: number;
    messages: number;
    eligible_threads: number;
    excluded_threads: number;
  };
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

// ── Date-windowed Gmail ingest (S16.0, demo-side control) ────────────────────

export interface GmailWindowRequest {
  /** YYYY-MM-DD (inclusive) or null/omitted for an open bound. */
  date_from?: string | null;
  date_to?: string | null;
  /** Safety cap inside the window. */
  max_messages?: number;
}

export interface GmailIngestRequest extends GmailWindowRequest {
  /** Live (persisting) ingest requires an explicit confirm. */
  confirm: boolean;
  /** DESTRUCTIVE: clear existing derived data before ingesting the window. */
  replace_snapshot?: boolean;
  /** Optional internal domains; persisted to mailbox.config when provided. */
  internal_domains?: string[] | null;
}

export interface GmailWindowResponse {
  date_from: string | null;
  date_to: string | null;
  open_ended: boolean;
  provider_filter_applied: boolean;
  count: number;
  is_estimate: boolean;
  cap_hit: boolean;
  persisted: boolean;
  /** "preview" | "replace" | "append_upsert" — honest description of writes. */
  mode: string;
  replaced: boolean;
  sync_token_disposition: string;
}

// ── Handoff package — creator draft/scope/generate (S17.3 backend, S17.4 UI) ──

export interface HandoffScopeData {
  date_from: string | null;
  date_to: string | null;
  included_project_ids: string[];
  included_person_ids: string[];
  included_thread_ids: string[];
  excluded_thread_ids: string[];
  excluded_message_id_headers: string[];
  allowed_domains: string[];
  keyword_filters: string[];
}

/**
 * Scope PATCH body. The backend scope PATCH is REPLACE-LIKE: any omitted array
 * field resets to empty (the request model defaults them to []), so a caller
 * MUST send the COMPLETE current scope, not a sparse patch. Typed as the full
 * shape (not Partial) to prevent accidental wipes of included/excluded sets.
 */
export type ScopeRequestBody = HandoffScopeData;

export interface HandoffClaim {
  id: string;
  kind: string; // open_loop | decision | ... (see spec)
  text: string;
  project_id: string | null;
  source_message_id_headers: string[];
  confidence: number;
}

export interface HandoffEvidence {
  message_id_header: string;
  subject: string;
  sender_display: string;
  sender_domain: string;
  date: string; // ISO 8601 ("" if unknown)
  body_snapshot: string;
  source_type: string | null;
}

/** Creator-only reason an empty generated candidate is empty (S17.13). */
export type GenerationDiagnosticCode =
  | "no_events_for_mailbox"
  | "no_events_in_scope"
  | "all_events_excluded_by_policy";

export interface GenerationDiagnostic {
  code: GenerationDiagnosticCode | string;
  event_count: number;
}

export interface HandoffPackage {
  id: string;
  mailbox_id: string;
  creator_email: string;
  status: string; // draft | generated | published | revoked | ...
  reason: string;
  title: string;
  version: number;
  created_at: string;
  updated_at: string;
  /** Set once published (S17.5); null while draft/generated. */
  published_at?: string | null;
  expires_at?: string | null;
  revoked_at?: string | null;
  scope: HandoffScopeData;
  claims: HandoffClaim[];
  evidence: HandoffEvidence[];
  /** Creator-only aggregate exclusion counts (never shown to a recipient). */
  exclusion_counts: Record<string, number>;
  /** Creator-only: why an empty generated candidate is empty (S17.13); else null. */
  generation?: GenerationDiagnostic | null;
}

/** Publish request (creator). Mirrors services/api/schemas/handoff.py PublishRequest. */
export interface PublishRequest {
  recipient_email: string;
  /** Override the 30-day default validity (1–365). Omit to keep the default. */
  expires_in_days?: number | null;
}

/**
 * Publish response (creator), returned ONCE. `capability_code` is the only time
 * the raw code is exposed — the server keeps only its hash. Place it in the
 * recipient URL *fragment* via `share_fragment` (`#c=<code>`), never a
 * path/query, and never persist it. Mirrors PublishResponse.
 */
export interface PublishResponse {
  package: HandoffPackage;
  recipient_email: string;
  expires_at: string;
  capability_code: string;
  share_fragment: string;
}

// ── Handoff package — recipient view (S17.5 backend, S17.6 UI) ────────────────
//
// These types mirror services/api/schemas/handoff.py exactly (RecipientSession-
// Response / RecipientPackageOut and friends). The recipient shape is a strict
// subset of the creator package: it deliberately carries NO mailbox_id, NO
// exclusion counts, and NO Gmail/source/open_url link — the recipient reads
// snapshotted content only, never the live mailbox.

export interface RecipientSession {
  session_token: string; // short-lived bearer; memory-only, sent as Authorization
  expires_at: string; // ISO 8601 session expiry
  package_id: string;
}

export interface RecipientClaim {
  id: string;
  kind: string; // open_loop | decision | blocker | project_state | briefing | person_note
  text: string;
  project_id: string | null;
  source_message_id_headers: string[];
  confidence: number;
}

export interface RecipientEvidence {
  message_id_header: string;
  subject: string;
  sender_display: string;
  sender_domain: string;
  date: string; // ISO 8601 ("" if unknown)
  body_snapshot: string;
  source_type: string | null;
}

/** Global, package-invariant posture — a constant statement carrying no counts
 * and no per-topic signal (so it can never act as an existence oracle). */
export interface RecipientPrivacyPosture {
  scope_limited: boolean;
  sensitive_excluded: boolean;
  note: string;
}

export interface RecipientPackage {
  package_id: string;
  title: string;
  reason: string;
  creator_email: string;
  published_at: string | null;
  expires_at: string | null;
  claims: RecipientClaim[];
  evidence: RecipientEvidence[];
  privacy_posture: RecipientPrivacyPosture;
}

// ── Recipient package-local ask (S17.9) ──────────────────────────────────────
// Mirrors services/api/schemas/handoff.py RecipientAskResponse. The answer is
// deterministic and package-local: every cited evidence row is an in-package
// HandoffEvidence, and `answered: false` is the SAME neutral result for
// no-match / sensitive / unknown / insufficient — never an existence oracle.

export interface RecipientAnswerClaim {
  id: string;
  kind: string;
  text: string;
  source_message_id_headers: string[];
}

export interface RecipientAskResponse {
  answered: boolean;
  message: string;
  claims: RecipientAnswerClaim[];
  evidence: RecipientEvidence[];
}

// ── S31 Admin / Audit Viewer DTOs (mirror services/admin/contracts.py) ────────
// Safe metadata only — these types deliberately contain NO evidence body, claim
// text, scope detail, source headers, raw job params/errors, tokens, or vault_ref.

export interface PackageAdminSummary {
  id: string;
  mailbox_id: string;
  title: string;
  status: string;
  version: number;
  lineage_id: string;
  creator_email: string;
  reason_category: string;
  recipient_email: string | null; // full for admin; domain/masked for reviewer
  created_at: string | null;
  published_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
}

export interface PackageAdminDetail extends PackageAdminSummary {
  policy_mode: string;
  supersedes_package_id: string | null;
  exported_at: string | null;
  recipient_state: string | null;
  claim_count: number;
  evidence_count: number;
}

export interface PackageAuditEventView {
  package_id: string;
  lineage_id: string | null;
  actor: string;
  action: string;
  ts: string | null;
  safe_metadata: Record<string, unknown>;
}

export interface ProviderAccountAdminView {
  id: string | null;
  mailbox_id: string | null;
  owner_user_id: string | null;
  provider: string;
  provider_account_email: string | null;
  scopes_granted: string[];
  status: string;
  connected_at: string | null;
  last_verified_at: string | null;
  disconnected_at: string | null;
  mismatch_reason: string | null;
}

export interface JobAdminView {
  id: string;
  job_type: string;
  status: string;
  tenant_id: string;
  mailbox_id: string | null;
  attempt: number;
  max_attempts: number;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  next_retry_at: string | null;
  progress_safe: Record<string, unknown>;
  summary: string | null;
  error_category: string | null;
}

export interface AuditEventView {
  actor: string;
  action: string;
  scope: string | null;
  ts: string | null;
  finished_at: string | null;
  message_count: number | null;
  mailbox_id: string;
}

export interface ExclusionSummaryItem {
  exclusion_type: string;
  aggregate_label: string;
  count: number;
}

export interface ExclusionSummaryView {
  by_type: ExclusionSummaryItem[];
  total_excluded: number;
}

export interface ReadinessCheckView {
  name: string;
  status: string;
  message: string;
}

export interface ReadinessSummaryView {
  ready: boolean;
  checks: ReadinessCheckView[];
}

export interface TenantOpsOverview {
  package_counts_by_status: Record<string, number>;
  active_provider_accounts: number;
  job_counts_by_status: Record<string, number>;
  degraded_readiness: boolean;
}
