import type { ProjectSummary } from "../api/types";
import { cleanProjectLabel } from "../utils/projectLabels";
import { buildSuggestions } from "../utils/suggestions";
import {
  INDICATOR_DOT_CLASS,
  type Indicator,
  type ReadinessItem,
} from "../utils/readiness";

interface OverviewProps {
  ownerName: string | null;
  contactCount: number | null;
  projectCount: number | null;
  projects: ProjectSummary[];
  readinessItems: ReadinessItem[];
  /** Ask a suggested question (routes to Cover for Me and auto-asks). */
  onAskSuggested: (query: string) => void;
  /** Open a project (routes to Projects and selects it). */
  onOpenProject: (projectId: string) => void;
}

function StatCard({
  label,
  value,
  indicator,
  hint,
}: {
  label: string;
  value: string;
  indicator?: Indicator;
  hint?: string;
}) {
  return (
    <div className="overview-stat" title={hint}>
      <div className="overview-stat-label">
        {indicator ? (
          <span className={INDICATOR_DOT_CLASS[indicator]} aria-hidden="true" />
        ) : null}
        {label}
      </div>
      <div className="overview-stat-value">{value}</div>
    </div>
  );
}

/**
 * Workspace overview (S12) — the default /app entry. Orients a user before they
 * dive into a specific surface: mailbox readiness at a glance, suggested
 * questions for people who do not yet know what to ask, and the top projects as
 * quick entry points. Reuses data the workspace already loaded plus the shared
 * /api/preflight readiness; it never blocks on preflight.
 */
export function Overview({
  ownerName,
  contactCount,
  projectCount,
  projects,
  readinessItems,
  onAskSuggested,
  onOpenProject,
}: OverviewProps) {
  const byKey = Object.fromEntries(readinessItems.map((i) => [i.key, i]));
  const fmtCount = (n: number | null) => (n === null ? "—" : String(n));

  const suggestions = buildSuggestions(projects);
  const topProjects = projects.slice(0, 6);

  return (
    <div className="overview">
      <header className="overview-header">
        <h1>Workspace overview</h1>
        <p className="overview-sub">
          {ownerName
            ? `Oriented around ${ownerName}'s authorized mailbox.`
            : "Orient yourself before diving into a surface."}{" "}
          Everything here is built from structured email evidence — answers are cited.
        </p>
      </header>

      {/* At-a-glance readiness + counts */}
      <section className="overview-stats">
        <StatCard label="People" value={fmtCount(contactCount)} hint="Contacts in the network map" />
        <StatCard label="Projects" value={fmtCount(projectCount)} hint="Materialized projects" />
        <StatCard
          label="Embeddings"
          value={labelFor(byKey.embeddings?.indicator)}
          indicator={byKey.embeddings?.indicator}
          hint={byKey.embeddings?.hint}
        />
        <StatCard
          label="Retrieval"
          value={labelFor(byKey.retrieval?.indicator)}
          indicator={byKey.retrieval?.indicator}
          hint={byKey.retrieval?.hint}
        />
        <StatCard
          label="Synthesis"
          value={labelFor(byKey.synthesis?.indicator)}
          indicator={byKey.synthesis?.indicator}
          hint={byKey.synthesis?.hint}
        />
      </section>

      {/* Suggested questions */}
      <section className="overview-section">
        <h2>Ask something</h2>
        <p className="overview-section-sub">
          Not sure where to start? Try a question — every answer is grounded in
          cited messages.
        </p>
        <div className="overview-suggestions">
          {suggestions.map((q) => (
            <button
              key={q}
              type="button"
              className="suggestion-chip"
              onClick={() => onAskSuggested(q)}
            >
              {q}
            </button>
          ))}
        </div>
      </section>

      {/* Top projects */}
      <section className="overview-section">
        <h2>Projects</h2>
        {topProjects.length === 0 ? (
          <p className="overview-section-sub">
            No projects materialized yet for this mailbox.
          </p>
        ) : (
          <ul className="overview-projects" role="list">
            {topProjects.map((p) => {
              const { display, uncategorized, lowConfidence } = cleanProjectLabel(
                p.label,
                p.confidence,
              );
              return (
                <li key={p.id}>
                  <button
                    type="button"
                    className="overview-project-row"
                    onClick={() => onOpenProject(p.id)}
                    title={p.label}
                  >
                    <span
                      className={`overview-project-label${
                        uncategorized ? " is-uncategorized" : ""
                      }`}
                    >
                      {display}
                    </span>
                    <span className="overview-project-meta">
                      {lowConfidence ? (
                        <span className="project-card-lowconf">low confidence</span>
                      ) : null}
                      <span>{p.thread_count} thread{p.thread_count === 1 ? "" : "s"}</span>
                      <span className="overview-project-state">{p.state}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}

function labelFor(indicator: Indicator | undefined): string {
  switch (indicator) {
    case "ok":
      return "Ready";
    case "warn":
      return "Limited";
    case "bad":
      return "Unavailable";
    default:
      return "Unknown";
  }
}
