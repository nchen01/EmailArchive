import { useState } from "react";
import {
  fetchProjectSummary,
  SummariesNotConfiguredError,
} from "../api/client";
import type {
  ActivityItem,
  ProjectDetailData,
  SynthesisResult,
} from "../api/types";
import { roleColor, roleLabel } from "../utils/roleColors";

interface ProjectDetailProps {
  detail: ProjectDetailData;
  /** Mailbox + project ids, needed to trigger L3 synthesis (spec 02 §6). */
  mailboxId: string;
  /** Deep-link a thread back to its source (provenance, spec 02 §5). */
  onThreadClick?: (threadId: string) => void;
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

// Epistemic grade order: outcome > did > proposed (spec 02 §6). A `proposed`
// item must never render under the "confirmed/outcome" heading.
const GRADE_ORDER: { type: string; heading: string }[] = [
  { type: "outcome", heading: "Confirmed outcomes" },
  { type: "did", heading: "Actions taken" },
  { type: "proposed", heading: "Proposed / intended" },
];

function CitationChips({ ids }: { ids: string[] }) {
  return (
    <span className="activity-citations">
      {ids.map((id) => (
        <span key={id} className="citation-chip" title={id}>
          {id}
        </span>
      ))}
    </span>
  );
}

/**
 * Full project detail (spec 02 §5): header, metric row, who-to-ask, members by
 * role, and recent threads with source-thread deep links.
 *
 * "What's been done" (Events) is S4 and intentionally absent; the API returns
 * an empty ``activity`` list in S3.
 */
export function ProjectDetail({
  detail,
  mailboxId,
  onThreadClick,
}: ProjectDetailProps) {
  // Group members by role for the members-by-role section.
  const byRole = new Map<string, typeof detail.members>();
  for (const m of detail.members) {
    const list = byRole.get(m.role) ?? [];
    list.push(m);
    byRole.set(m.role, list);
  }
  const roles = [...byRole.keys()].sort();

  // Group activity Events by epistemic grade.
  const byGrade = new Map<string, ActivityItem[]>();
  for (const a of detail.activity) {
    const list = byGrade.get(a.type) ?? [];
    list.push(a);
    byGrade.set(a.type, list);
  }

  // L3 synthesis state ("Generate summary").
  const [summary, setSummary] = useState<SynthesisResult | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const generateSummary = async () => {
    setSummaryLoading(true);
    setSummaryError(null);
    setSummary(null);
    try {
      const res = await fetchProjectSummary(mailboxId, detail.id);
      setSummary(res);
    } catch (err) {
      if (err instanceof SummariesNotConfiguredError) {
        setSummaryError("Summaries are not configured.");
      } else {
        setSummaryError("Could not generate summary. Please try again.");
      }
    } finally {
      setSummaryLoading(false);
    }
  };

  return (
    <section className="project-detail">
      {/* Header */}
      <header className="project-detail-header">
        <h2>{detail.label}</h2>
        <span className={`project-state-pill state-${detail.state}`}>
          {detail.state}
        </span>
        <span
          className="project-confidence-pill"
          title="Clustering confidence"
        >
          {(detail.confidence * 100).toFixed(0)}% confidence
        </span>
      </header>

      {/* Metric row */}
      <div className="project-metric-row">
        <div>
          <strong>{detail.metrics.members}</strong> members
        </div>
        <div>
          <strong>{detail.metrics.threads}</strong> threads
        </div>
        <div>
          last activity <strong>{fmtDate(detail.metrics.last_activity)}</strong>
        </div>
      </div>

      {/* Who to ask */}
      <div className="project-who-to-ask">
        <h3>Who to ask</h3>
        {detail.who_to_ask.length === 0 ? (
          <p className="muted">No contacts ranked yet.</p>
        ) : (
          <ul role="list">
            {detail.who_to_ask.map((c) => (
              <li key={c.person_id} className="who-to-ask-chip">
                <span
                  className="role-dot"
                  style={{ backgroundColor: roleColor(c.role) }}
                />
                <span className="who-to-ask-name">{c.name}</span>
                <span className="who-to-ask-role">{roleLabel(c.role)}</span>
                <span className="who-to-ask-count">
                  {c.in_project_count} msg
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Members by role */}
      <div className="project-members">
        <h3>Members</h3>
        {roles.map((role) => (
          <div key={role} className="member-role-group">
            <h4 style={{ color: roleColor(role) }}>{roleLabel(role)}</h4>
            <ul role="list">
              {byRole.get(role)!.map((m) => (
                <li key={m.person_id} className="member-row">
                  <span>{m.name}</span>
                  <span className="member-count">
                    {m.in_project_count} in-project msg
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      {/* Recent threads */}
      <div className="project-threads">
        <h3>Recent threads</h3>
        <ul role="list">
          {detail.recent_threads.map((t) => (
            <li key={t.thread_id} className="thread-row">
              <button
                type="button"
                className="thread-link"
                onClick={() => onThreadClick?.(t.thread_id)}
                title="Open source thread"
              >
                {t.subject || "(no subject)"}
              </button>
              <span className="thread-meta">
                {t.participants.length} participants · {fmtDate(t.last)}
              </span>
            </li>
          ))}
        </ul>
      </div>

      {/* What's been done — Events grouped by epistemic grade (spec 02 §6) */}
      <div className="project-activity">
        <h3>What's been done</h3>
        {detail.activity.length === 0 ? (
          <p className="muted">No evidenced activity in email yet.</p>
        ) : (
          GRADE_ORDER.map(({ type, heading }) => {
            const items = byGrade.get(type) ?? [];
            if (items.length === 0) return null;
            return (
              <div key={type} className={`activity-grade activity-grade-${type}`}>
                <h4>{heading}</h4>
                <ul role="list">
                  {items.map((a, i) => (
                    <li key={`${type}-${i}`} className="activity-row">
                      <span className="activity-summary">{a.summary}</span>
                      <CitationChips ids={a.source_message_ids} />
                    </li>
                  ))}
                </ul>
              </div>
            );
          })
        )}

        {/* Generate summary (L3 synthesis) */}
        <div className="project-summary">
          <button
            type="button"
            className="generate-summary-btn"
            onClick={generateSummary}
            disabled={summaryLoading}
          >
            {summaryLoading ? "Generating…" : "Generate summary"}
          </button>

          {summaryError ? (
            <div className="summary-error" role="alert">
              {summaryError}
            </div>
          ) : null}

          {summary ? (
            <div className="summary-result">
              {summary.claims.length === 0 ? (
                <p className="muted">
                  {summary.state ?? "No evidenced activity in email."}
                </p>
              ) : (
                <ul role="list">
                  {summary.claims.map((c, i) => (
                    <li key={i} className="summary-claim">
                      <span className="claim-text">{c.text}</span>
                      <CitationChips ids={c.source_message_ids} />
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}
