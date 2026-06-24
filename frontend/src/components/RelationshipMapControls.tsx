import type {
  ProjectSummary,
  RelationshipMapMode,
  RelationshipType,
} from "../api/types";
import { cleanProjectLabel } from "../utils/projectLabels";
import { REL_TYPE_LABEL } from "../utils/relationshipMap";

const MODES: { mode: RelationshipMapMode; label: string }[] = [
  { mode: "owner", label: "Owner tree" },
  { mode: "project", label: "Project tree" },
  { mode: "org", label: "Organization tree" },
  { mode: "graph", label: "Graph view" },
];

const ALL_TYPES: RelationshipType[] = [
  "direct_exchange",
  "thread_copresence",
  "project_copresence",
  "org_affiliation",
  "bridge",
];

const RECENCY_OPTIONS: { label: string; days: number | null }[] = [
  { label: "All time", days: null },
  { label: "Last 90 days", days: 90 },
  { label: "Last 180 days", days: 180 },
  { label: "Last year", days: 365 },
];

interface ControlsProps {
  mode: RelationshipMapMode;
  onModeChange: (m: RelationshipMapMode) => void;
  projects: ProjectSummary[];
  projectId: string | null;
  onProjectChange: (id: string) => void;
  activeTypes: Set<RelationshipType>;
  onToggleType: (t: RelationshipType) => void;
  recencyDays: number | null;
  onRecencyChange: (d: number | null) => void;
  minEvidence: number;
  onMinEvidenceChange: (n: number) => void;
}

/** Left-rail controls for the relationship map: mode, project root, relationship
 *  type filters, recency, and minimum evidence. */
export function RelationshipMapControls({
  mode,
  onModeChange,
  projects,
  projectId,
  onProjectChange,
  activeTypes,
  onToggleType,
  recencyDays,
  onRecencyChange,
  minEvidence,
  onMinEvidenceChange,
}: ControlsProps) {
  return (
    <div className="rel-controls">
      <div className="rel-control-group">
        <h3>View</h3>
        <div className="rel-mode-list">
          {MODES.map((m) => (
            <button
              key={m.mode}
              type="button"
              className={`rel-mode-btn${mode === m.mode ? " is-active" : ""}`}
              onClick={() => onModeChange(m.mode)}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>

      {mode === "project" ? (
        <div className="rel-control-group">
          <h3>Project root</h3>
          <select
            className="rel-select"
            value={projectId ?? ""}
            onChange={(e) => onProjectChange(e.target.value)}
          >
            <option value="" disabled>
              Select a project…
            </option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {cleanProjectLabel(p.label, p.confidence).display}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      <div className="rel-control-group">
        <h3>Relationship types</h3>
        <div className="rel-type-list">
          {ALL_TYPES.map((t) => (
            <label key={t} className="rel-type-row">
              <input
                type="checkbox"
                checked={activeTypes.has(t)}
                onChange={() => onToggleType(t)}
              />
              <span>{REL_TYPE_LABEL[t]}</span>
            </label>
          ))}
        </div>
      </div>

      <div className="rel-control-group">
        <h3>Recency</h3>
        <select
          className="rel-select"
          value={recencyDays ?? ""}
          onChange={(e) =>
            onRecencyChange(e.target.value === "" ? null : Number(e.target.value))
          }
        >
          {RECENCY_OPTIONS.map((o) => (
            <option key={o.label} value={o.days ?? ""}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      <div className="rel-control-group">
        <h3>Min. evidence</h3>
        <input
          type="number"
          min={1}
          max={20}
          value={minEvidence}
          onChange={(e) => onMinEvidenceChange(Math.max(1, Number(e.target.value) || 1))}
          className="rel-select"
        />
        <p className="rel-control-note">
          Evidence count is communication volume, not importance.
        </p>
      </div>
    </div>
  );
}
