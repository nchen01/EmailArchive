import type { ProjectDetailData } from "../api/types";
import { roleColor, roleLabel } from "../utils/roleColors";

interface ProjectDetailProps {
  detail: ProjectDetailData;
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

/**
 * Full project detail (spec 02 §5): header, metric row, who-to-ask, members by
 * role, and recent threads with source-thread deep links.
 *
 * "What's been done" (Events) is S4 and intentionally absent; the API returns
 * an empty ``activity`` list in S3.
 */
export function ProjectDetail({ detail, onThreadClick }: ProjectDetailProps) {
  // Group members by role for the members-by-role section.
  const byRole = new Map<string, typeof detail.members>();
  for (const m of detail.members) {
    const list = byRole.get(m.role) ?? [];
    list.push(m);
    byRole.set(m.role, list);
  }
  const roles = [...byRole.keys()].sort();

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
    </section>
  );
}
