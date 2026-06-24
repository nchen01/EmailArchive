import type { ProjectSummary } from "../api/types";
import { cleanProjectLabel } from "../utils/projectLabels";

interface ProjectListProps {
  projects: ProjectSummary[];
  selectedProjectId: string | null;
  onSelect: (projectId: string) => void;
}

const STATE_COLORS: Record<string, string> = {
  active: "#27AE60",
  stale: "#95A5A6",
};

function stateColor(state: string): string {
  return STATE_COLORS[state] ?? STATE_COLORS.stale;
}

function formatSpan(start: string, end: string): string {
  const opts: Intl.DateTimeFormatOptions = { month: "short", year: "numeric" };
  const s = new Date(start).toLocaleDateString(undefined, opts);
  const e = new Date(end).toLocaleDateString(undefined, opts);
  return s === e ? s : `${s} – ${e}`;
}

/**
 * Sidebar / card grid of projects (spec 02 §3). Projects arrive already sorted
 * by confidence DESC from the API; we render them as selectable cards.
 */
export function ProjectList({
  projects,
  selectedProjectId,
  onSelect,
}: ProjectListProps) {
  if (projects.length === 0) {
    return <div className="project-list-empty">No projects yet.</div>;
  }

  return (
    <ul className="project-list" role="list">
      {projects.map((p) => {
        const selected = p.id === selectedProjectId;
        const { display, uncategorized, lowConfidence } = cleanProjectLabel(
          p.label,
          p.confidence,
        );
        return (
          <li key={p.id}>
            <button
              type="button"
              className={`project-card${selected ? " is-selected" : ""}`}
              onClick={() => onSelect(p.id)}
              aria-pressed={selected}
            >
              <div className="project-card-header">
                <span
                  className={`project-card-label${
                    uncategorized ? " is-uncategorized" : ""
                  }`}
                  title={p.label}
                >
                  {display}
                </span>
                {lowConfidence ? (
                  <span
                    className="project-card-lowconf"
                    title={`Low clustering confidence (${(p.confidence * 100).toFixed(0)}%)`}
                  >
                    low confidence
                  </span>
                ) : null}
                <span
                  className="project-card-state"
                  style={{ backgroundColor: stateColor(p.state) }}
                  title={`State: ${p.state}`}
                >
                  {p.state}
                </span>
              </div>
              <div className="project-card-meta">
                <span>{formatSpan(p.start, p.end)}</span>
                <span>
                  {p.member_count} member{p.member_count === 1 ? "" : "s"} ·{" "}
                  {p.thread_count} thread{p.thread_count === 1 ? "" : "s"}
                </span>
              </div>
              <div
                className="project-card-confidence"
                title={`Clustering confidence: ${(p.confidence * 100).toFixed(0)}%`}
              >
                <div
                  className="project-card-confidence-bar"
                  style={{ width: `${Math.round(p.confidence * 100)}%` }}
                />
              </div>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
