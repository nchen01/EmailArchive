import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D, {
  type ForceGraphMethods,
  type NodeObject,
} from "react-force-graph-2d";
import { errorKindTitle, type RelationshipMapQuery } from "../api/client";
import type {
  ProjectSummary,
  RelationshipEdge,
  RelationshipMapMode,
  RelationshipNode,
  RelationshipType,
} from "../api/types";
import { useRelationshipMap } from "../hooks/useRelationshipMap";
import {
  edgeColor,
  edgeDash,
  edgeWidth,
  NODE_COLOR,
} from "../utils/relationshipMap";
import { ErrorBanner } from "./ErrorBanner";
import { LoadingSpinner } from "./LoadingSpinner";
import {
  RelationshipDetailDrawer,
  type RelSelection,
} from "./RelationshipDetailDrawer";
import { RelationshipMapControls } from "./RelationshipMapControls";

interface RelationshipMapProps {
  mailboxId: string;
  projects: ProjectSummary[];
}

interface GraphNode {
  id: string;
  name: string;
  color: string;
  ntype: string;
  val: number;
  node: RelationshipNode;
  fx?: number;
  fy?: number;
  x?: number;
  y?: number;
}
interface GraphLink {
  source: string;
  target: string;
  edge: RelationshipEdge;
}

const ALL_TYPES: RelationshipType[] = [
  "direct_exchange",
  "thread_copresence",
  "project_copresence",
  "org_affiliation",
  "bridge",
];

export function RelationshipMap({ mailboxId, projects }: RelationshipMapProps) {
  const [mode, setMode] = useState<RelationshipMapMode>("owner");
  const [projectId, setProjectId] = useState<string | null>(null);
  const [activeTypes, setActiveTypes] = useState<Set<RelationshipType>>(
    new Set(ALL_TYPES),
  );
  const [recencyDays, setRecencyDays] = useState<number | null>(null);
  const [minEvidence, setMinEvidence] = useState(1);
  const [selection, setSelection] = useState<RelSelection | null>(null);

  const fgRef = useRef<ForceGraphMethods<GraphNode, GraphLink> | undefined>(
    undefined,
  );

  // Default the project root to the top project when entering project mode.
  useEffect(() => {
    if (mode === "project" && !projectId && projects.length > 0) {
      setProjectId(projects[0].id);
    }
  }, [mode, projectId, projects]);

  // Clear any open detail when the mailbox or mode changes (trust boundary).
  useEffect(() => {
    setSelection(null);
  }, [mailboxId, mode, projectId]);

  const query: RelationshipMapQuery = useMemo(() => {
    const allSelected = activeTypes.size === ALL_TYPES.length;
    return {
      mode,
      projectId: mode === "project" ? projectId : null,
      recencyDays,
      relationshipTypes: allSelected ? undefined : [...activeTypes].sort(),
    };
  }, [mode, projectId, recencyDays, activeTypes]);

  const { data, loading, error, errorKind } = useRelationshipMap(mailboxId, query);

  // Client-side minimum-evidence filter (drops below threshold, then orphans).
  const graphData = useMemo(() => {
    if (!data) return { nodes: [] as GraphNode[], links: [] as GraphLink[] };
    const keptEdges = data.edges.filter((e) => e.evidence_count >= minEvidence);
    const referenced = new Set<string>();
    for (const e of keptEdges) {
      referenced.add(e.source_id);
      referenced.add(e.target_id);
    }
    if (data.root) referenced.add(data.root.id);

    const nodes: GraphNode[] = data.nodes
      .filter((n) => referenced.has(n.id))
      .map((n) => {
        const isRoot = data.root?.id === n.id;
        return {
          id: n.id,
          name: n.label,
          color: NODE_COLOR[n.node_type] ?? "#64748b",
          ntype: n.node_type,
          val: isRoot ? 14 : n.node_type === "person" ? 6 : 9,
          node: n,
          ...(isRoot ? { fx: 0, fy: 0 } : {}),
        };
      });
    const links: GraphLink[] = keptEdges.map((e) => ({
      source: e.source_id,
      target: e.target_id,
      edge: e,
    }));
    return { nodes, links };
  }, [data, minEvidence]);

  const nodeLabels = useMemo(() => {
    const m: Record<string, string> = {};
    for (const n of data?.nodes ?? []) m[n.id] = n.label;
    return m;
  }, [data]);

  useEffect(() => {
    const fg = fgRef.current;
    if (!fg) return;
    fg.d3Force("charge")?.strength(-160);
    fg.d3Force("link")?.distance(70);
  }, [graphData]);

  const toggleType = (t: RelationshipType) =>
    setActiveTypes((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      // Never allow zero selected (would mean "no filter" ambiguity); keep one.
      return next.size === 0 ? new Set([t]) : next;
    });

  const counts = data?.generated_from;

  return (
    <div className="rel-layout">
      <RelationshipMapControls
        mode={mode}
        onModeChange={setMode}
        projects={projects}
        projectId={projectId}
        onProjectChange={setProjectId}
        activeTypes={activeTypes}
        onToggleType={toggleType}
        recencyDays={recencyDays}
        onRecencyChange={setRecencyDays}
        minEvidence={minEvidence}
        onMinEvidenceChange={setMinEvidence}
      />

      <div className="rel-canvas">
        {loading ? (
          <div className="flex h-full items-center justify-center">
            <LoadingSpinner label="Deriving relationships…" />
          </div>
        ) : error ? (
          <div className="mx-auto mt-8 max-w-lg px-4">
            <ErrorBanner title={errorKindTitle(errorKind)} message={error} />
          </div>
        ) : graphData.nodes.length === 0 ? (
          <div className="flex h-full items-center justify-center px-6 text-center text-slate-500">
            <div>
              <p className="text-lg font-medium text-slate-700">
                No relationships to show.
              </p>
              <p className="mt-1 text-sm text-slate-400">
                {mode === "project" && !projectId
                  ? "Select a project root from the left."
                  : "No eligible (non-noise, non-sensitive) relationships matched these filters."}
              </p>
            </div>
          </div>
        ) : (
          <>
            <div className="rel-legend">
              <span><span className="rel-legend-line solid" /> Direct exchange</span>
              <span><span className="rel-legend-line dashed" /> Co-participation</span>
              <span><span className="rel-legend-line dotted" /> Org affiliation</span>
              <span className="rel-legend-note">
                Line thickness = evidence volume, not importance
                {counts ? ` · ${counts.eligible_threads} eligible / ${counts.excluded_threads} excluded threads` : ""}
              </span>
            </div>
            <ForceGraph2D
              ref={fgRef}
              graphData={graphData}
              nodeId="id"
              nodeVal="val"
              nodeColor="color"
              nodeLabel={(n: NodeObject<GraphNode>) =>
                `${n.name}${n.ntype !== "person" ? ` (${n.ntype})` : ""}`
              }
              linkColor={(l) => edgeColor((l as GraphLink).edge)}
              linkWidth={(l) => edgeWidth((l as GraphLink).edge)}
              linkLineDash={(l) => edgeDash((l as GraphLink).edge.relationship_type)}
              nodeRelSize={4}
              cooldownTicks={120}
              onNodeClick={(n: NodeObject<GraphNode>) =>
                setSelection({ kind: "node", node: n.node })
              }
              onLinkClick={(l) =>
                setSelection({ kind: "edge", edge: (l as GraphLink).edge })
              }
              nodeCanvasObjectMode={() => "after"}
              nodeCanvasObject={(node: NodeObject<GraphNode>, ctx, globalScale) => {
                const n = node as GraphNode;
                if (n.x == null || n.y == null) return;
                const radius = Math.sqrt(Math.max(0, n.val)) * 4;
                if (n.ntype !== "person" || globalScale > 1.1) {
                  const fontSize = 12 / globalScale;
                  ctx.font = `${fontSize}px sans-serif`;
                  ctx.textAlign = "center";
                  ctx.textBaseline = "top";
                  ctx.fillStyle = "#334155";
                  ctx.fillText(n.name, n.x, n.y + radius + 1);
                }
              }}
            />
          </>
        )}
      </div>

      <RelationshipDetailDrawer
        selection={selection}
        nodeLabels={nodeLabels}
        onClose={() => setSelection(null)}
      />
    </div>
  );
}
