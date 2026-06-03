import { useEffect, useMemo, useRef } from "react";
import ForceGraph2D, {
  type ForceGraphMethods,
  type NodeObject,
} from "react-force-graph-2d";
import type { Node as ApiNode, NetworkMapData } from "../api/types";
import { OWNER_COLOR, roleColor } from "../utils/roleColors";

interface NetworkMapProps {
  data: NetworkMapData;
  onNodeClick: (node: ApiNode) => void;
  activeRoles: Set<string>;
  selectedPersonId: string | null;
}

// Graph node: the API node fields plus react-force-graph-2d display fields.
interface GraphNode {
  id: string;
  name: string;
  color: string;
  val: number;
  isOwner: boolean;
  // Owner gets pinned coordinates.
  fx?: number;
  fy?: number;
  apiNode?: ApiNode;
  // mutated by the engine
  x?: number;
  y?: number;
}

interface GraphLink {
  source: string;
  target: string;
  width: number;
  color: string;
}

const MIN_RADIUS = 3;
const MAX_RADIUS = 15;

// Normalize a weight in [0, maxWeight] to a node radius in [MIN, MAX].
function normalizeRadius(weight: number, maxWeight: number): number {
  if (maxWeight <= 0) return MIN_RADIUS;
  const t = Math.min(1, Math.max(0, weight / maxWeight));
  return MIN_RADIUS + t * (MAX_RADIUS - MIN_RADIUS);
}

export function NetworkMap({
  data,
  onNodeClick,
  activeRoles,
  selectedPersonId,
}: NetworkMapProps) {
  const fgRef = useRef<ForceGraphMethods<GraphNode, GraphLink> | undefined>(
    undefined,
  );

  const graphData = useMemo(() => {
    const maxWeight = data.nodes.reduce((m, n) => Math.max(m, n.weight), 0);

    const visibleNodes = data.nodes.filter((n) => activeRoles.has(n.role));
    const visibleIds = new Set(visibleNodes.map((n) => n.person_id));

    const ownerNode: GraphNode = {
      id: data.owner.person_id,
      name: data.owner.name,
      color: OWNER_COLOR,
      val: MAX_RADIUS + 6,
      isOwner: true,
      fx: 0,
      fy: 0,
    };

    const contactNodes: GraphNode[] = visibleNodes.map((n) => ({
      id: n.person_id,
      name: n.name,
      color: roleColor(n.role),
      val: normalizeRadius(n.weight, maxWeight),
      isOwner: false,
      apiNode: n,
    }));

    const links: GraphLink[] = data.edges
      .filter((e) => visibleIds.has(e.person_id))
      .map((e) => ({
        source: data.owner.person_id,
        target: e.person_id,
        // weight is bounded ~[0,1] by the L1 formula -> width ~[0,3].
        width: Math.max(0.5, e.weight * 3),
        color: "#cccccc",
      }));

    return { nodes: [ownerNode, ...contactNodes], links };
  }, [data, activeRoles]);

  // Tune the force simulation once the engine is available.
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg) return;
    // ForceFn carries the d3-force builder methods via its index signature.
    fg.d3Force("charge")?.strength(-150);
    fg.d3Force("link")?.distance(100);
  }, [graphData]);

  return (
    <ForceGraph2D
      ref={fgRef}
      graphData={graphData}
      nodeId="id"
      nodeVal="val"
      nodeColor="color"
      nodeLabel={(n: NodeObject<GraphNode>) =>
        n.isOwner
          ? `${n.name} (owner)`
          : `${n.name}${n.apiNode ? ` — ${n.apiNode.role}` : ""}`
      }
      linkWidth={(l) => (l as GraphLink).width}
      linkColor={(l) => (l as GraphLink).color}
      nodeRelSize={4}
      cooldownTicks={120}
      onNodeClick={(n: NodeObject<GraphNode>) => {
        if (!n.isOwner && n.apiNode) onNodeClick(n.apiNode);
      }}
      nodeCanvasObjectMode={() => "after"}
      nodeCanvasObject={(node: NodeObject<GraphNode>, ctx, globalScale) => {
        const n = node as GraphNode;
        if (n.x == null || n.y == null) return;
        const radius = Math.sqrt(Math.max(0, n.val)) * 4;

        // Highlight ring around the selected contact.
        if (selectedPersonId && n.id === selectedPersonId) {
          ctx.beginPath();
          ctx.arc(n.x, n.y, radius + 3, 0, 2 * Math.PI);
          ctx.strokeStyle = "#111827";
          ctx.lineWidth = 2 / globalScale;
          ctx.stroke();
        }

        // Label the owner node always; others on a readable zoom level.
        if (n.isOwner || globalScale > 1.2) {
          const fontSize = 12 / globalScale;
          ctx.font = `${fontSize}px sans-serif`;
          ctx.textAlign = "center";
          ctx.textBaseline = "top";
          ctx.fillStyle = "#334155";
          ctx.fillText(n.name, n.x, n.y + radius + 1);
        }
      }}
    />
  );
}
