import { useMemo, useState } from "react";
import type {
  ProjectSummary,
  RelationshipMapMode,
  RelationshipType,
} from "../api/types";
import { cleanProjectLabel } from "../utils/projectLabels";
import {
  type ProjectRootSortMode,
  REL_TYPE_LABEL,
  sortedProjectRoots,
} from "../utils/relationshipMap";
import type { OrgGroup } from "../utils/relationshipGraph";

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
  orgGroups: OrgGroup[];
  collapsedDomains: Set<string>;
  onToggleDomain: (domain: string) => void;
  onExpandAllOrgs: () => void;
  onCollapseAllOrgs: () => void;
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
  orgGroups,
  collapsedDomains,
  onToggleDomain,
  onExpandAllOrgs,
  onCollapseAllOrgs,
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
        <ProjectRootPicker
          projects={projects}
          projectId={projectId}
          onProjectChange={onProjectChange}
        />
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

      {orgGroups.length > 0 ? (
        <OrgCollapsePanel
          orgGroups={orgGroups}
          collapsedDomains={collapsedDomains}
          onToggleDomain={onToggleDomain}
          onExpandAllOrgs={onExpandAllOrgs}
          onCollapseAllOrgs={onCollapseAllOrgs}
        />
      ) : null}
    </div>
  );
}

/** How many members to show per org before the "Show more" affordance. */
const MEMBERS_SHOWN = 5;

/**
 * Groups people by organization/domain and lets the user collapse (hide from the
 * canvas) or expand each group — the primary readability lever for a busy map.
 * Group size is shown as a plain member count (how many people share the domain),
 * explicitly not a ranking of people or orgs. Each group's member list is itself
 * capped with a Show more / Show fewer control so a large org never floods the
 * rail.
 */
function OrgCollapsePanel({
  orgGroups,
  collapsedDomains,
  onToggleDomain,
  onExpandAllOrgs,
  onCollapseAllOrgs,
}: {
  orgGroups: OrgGroup[];
  collapsedDomains: Set<string>;
  onToggleDomain: (domain: string) => void;
  onExpandAllOrgs: () => void;
  onCollapseAllOrgs: () => void;
}) {
  return (
    <div className="rel-control-group">
      <div className="rel-org-head">
        <h3>Organizations</h3>
        <div className="rel-org-bulk">
          <button type="button" onClick={onExpandAllOrgs}>
            Expand all
          </button>
          <span aria-hidden>·</span>
          <button type="button" onClick={onCollapseAllOrgs}>
            Collapse all
          </button>
        </div>
      </div>
      <p className="rel-control-note rel-control-note-top">
        Collapse an organization to hide its people and de-clutter the map. The
        count is how many people share the domain — not importance or ranking.
      </p>
      <ul className="rel-org-list">
        {orgGroups.map((g) => (
          <OrgGroupRow
            key={g.domain}
            group={g}
            collapsed={collapsedDomains.has(g.domain)}
            onToggle={() => onToggleDomain(g.domain)}
          />
        ))}
      </ul>
    </div>
  );
}

function OrgGroupRow({
  group,
  collapsed,
  onToggle,
}: {
  group: OrgGroup;
  collapsed: boolean;
  onToggle: () => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const members = showAll ? group.members : group.members.slice(0, MEMBERS_SHOWN);
  const hiddenCount = group.members.length - members.length;

  return (
    <li className="rel-org-row">
      <div className="rel-org-row-head">
        <button
          type="button"
          className="rel-org-toggle"
          aria-expanded={!collapsed}
          onClick={onToggle}
          title={collapsed ? "Show these people on the map" : "Hide these people from the map"}
        >
          <span className="rel-org-caret" aria-hidden>
            {collapsed ? "▸" : "▾"}
          </span>
          <span className="rel-org-name">
            {group.label}
            {group.internal ? <span className="rel-org-tag">internal</span> : null}
          </span>
          <span className="rel-org-count" title="Number of people sharing this domain">
            {group.members.length}
          </span>
        </button>
      </div>
      <ul className="rel-org-members">
        {members.map((m) => (
          <li key={m.id} className="rel-org-member">
            {m.label}
          </li>
        ))}
      </ul>
      {group.members.length > MEMBERS_SHOWN ? (
        <button
          type="button"
          className="rel-org-showmore"
          onClick={() => setShowAll((v) => !v)}
        >
          {showAll ? "Show fewer" : `Show ${hiddenCount} more`}
        </button>
      ) : null}
    </li>
  );
}

/** Project-root selector: best-first ordered, with a filter once the list grows. */
function ProjectRootPicker({
  projects,
  projectId,
  onProjectChange,
}: {
  projects: ProjectSummary[];
  projectId: string | null;
  onProjectChange: (id: string) => void;
}) {
  const [filter, setFilter] = useState("");
  const [sortMode, setSortMode] = useState<ProjectRootSortMode>("recommended");
  const SHOW_SEARCH_AT = 8;

  const ordered = useMemo(
    () => sortedProjectRoots(projects, sortMode),
    [projects, sortMode],
  );
  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return ordered;
    return ordered.filter((p) => {
      const display = cleanProjectLabel(p.label, p.confidence).display.toLowerCase();
      return display.includes(q) || p.label.toLowerCase().includes(q);
    });
  }, [ordered, filter]);

  // Always keep the currently-selected project in the option list, even when the
  // filter would exclude it, so the native <select> never shows a blank/confusing
  // value and projectId never points at a hidden option.
  const options = useMemo(() => {
    const selected = projectId ? ordered.find((p) => p.id === projectId) : undefined;
    if (selected && !visible.some((p) => p.id === selected.id)) {
      return [selected, ...visible];
    }
    return visible;
  }, [ordered, visible, projectId]);

  return (
    <div className="rel-control-group">
      <h3>Project root</h3>
      <p className="rel-control-note rel-control-note-top">
        Select a project root to redraw the map.
      </p>
      <label className="rel-sort-label">
        Sort
        <select
          className="rel-select rel-sort-select"
          value={sortMode}
          onChange={(e) => setSortMode(e.target.value as ProjectRootSortMode)}
          aria-label="Sort project roots"
        >
          <option value="recommended">Recommended</option>
          <option value="recent">Recent</option>
          <option value="relationship_rich">Relationship-rich</option>
          <option value="az">A–Z</option>
        </select>
      </label>
      {projects.length > SHOW_SEARCH_AT ? (
        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder={`Filter ${projects.length} projects…`}
          className="rel-select rel-project-filter"
          aria-label="Filter projects"
        />
      ) : null}
      <select
        className="rel-select"
        value={projectId ?? ""}
        onChange={(e) => onProjectChange(e.target.value)}
      >
        <option value="" disabled>
          Select a project…
        </option>
        {options.map((p) => (
          <option key={p.id} value={p.id}>
            {cleanProjectLabel(p.label, p.confidence).display}
          </option>
        ))}
      </select>
    </div>
  );
}
