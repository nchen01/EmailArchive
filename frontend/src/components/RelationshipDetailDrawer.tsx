import type { RelationshipEdge, RelationshipNode } from "../api/types";
import { REL_TYPE_LABEL } from "../utils/relationshipMap";

export type RelSelection =
  | { kind: "node"; node: RelationshipNode }
  | { kind: "edge"; edge: RelationshipEdge };

interface DrawerProps {
  selection: RelSelection | null;
  /** id → display label for resolving endpoints / project ids. */
  nodeLabels: Record<string, string>;
  onClose: () => void;
}

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/**
 * Right drawer showing why two nodes are connected, or details of a node.
 * Evidence-forward and volume-honest: it states the relationship type, a plain
 * explanation, evidence count (explicitly "not importance"), shared projects /
 * threads, and message-id citations when available. It never shows raw bodies or
 * sensitive content (the backend excludes sensitive threads from derivation).
 */
export function RelationshipDetailDrawer({
  selection,
  nodeLabels,
  onClose,
}: DrawerProps) {
  const open = selection !== null;
  const labelOf = (id: string) => nodeLabels[id] ?? id;

  return (
    <aside
      className={`fixed right-0 top-0 z-30 flex h-full w-[360px] flex-col border-l border-slate-200 bg-white shadow-xl transition-transform duration-300 ${
        open ? "translate-x-0" : "translate-x-full"
      }`}
      aria-hidden={!open}
    >
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <h2 className="text-sm font-semibold text-slate-600">
          {selection?.kind === "edge" ? "Relationship detail" : "Node detail"}
        </h2>
        <button
          type="button"
          onClick={onClose}
          className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
          aria-label="Close detail"
        >
          ✕
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {selection?.kind === "node" ? (
          <NodeDetail node={selection.node} />
        ) : selection?.kind === "edge" ? (
          <EdgeDetail edge={selection.edge} labelOf={labelOf} fmtDate={fmtDate} />
        ) : null}
      </div>
    </aside>
  );
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="mb-3">
      <div className="text-[11px] uppercase tracking-wide text-slate-400">{label}</div>
      <div className="text-sm text-slate-800">{value}</div>
    </div>
  );
}

function NodeDetail({ node }: { node: RelationshipNode }) {
  const md = node.metadata ?? {};
  return (
    <div>
      <Field label="Name" value={node.label} />
      <Field label="Type" value={node.node_type} />
      {node.subtitle ? <Field label="Detail" value={node.subtitle} /> : null}
      {node.role ? <Field label="Role" value={node.role} /> : null}
      {typeof md.org_domain === "string" && md.org_domain ? (
        <Field label="Organization" value={md.org_domain} />
      ) : null}
      {typeof md.project_count === "number" ? (
        <Field label="Projects" value={md.project_count} />
      ) : null}
      {md.is_bridge === true ? (
        <Field
          label="Bridge contact"
          value={`Appears across ${md.project_count ?? "multiple"} projects`}
        />
      ) : null}
    </div>
  );
}

function EdgeDetail({
  edge,
  labelOf,
  fmtDate,
}: {
  edge: RelationshipEdge;
  labelOf: (id: string) => string;
  fmtDate: (iso: string | null) => string;
}) {
  return (
    <div>
      <Field
        label="Relationship"
        value={REL_TYPE_LABEL[edge.relationship_type]}
      />
      <Field
        label="Between"
        value={`${labelOf(edge.source_id)} ↔ ${labelOf(edge.target_id)}`}
      />
      <Field label="Why connected" value={edge.explanation} />
      <Field
        label="Evidence count"
        value={`${edge.evidence_count} (volume, not importance)`}
      />
      {edge.muted ? (
        <Field label="Note" value="Weak or stale relationship (shown muted)." />
      ) : null}
      {edge.project_ids.length > 0 ? (
        <Field
          label={`Shared projects (${edge.project_ids.length})`}
          value={edge.project_ids.map(labelOf).join(", ")}
        />
      ) : null}
      {edge.thread_ids.length > 0 ? (
        <Field label="Shared threads" value={edge.thread_ids.length} />
      ) : null}
      <Field
        label="Active"
        value={`${fmtDate(edge.first_seen)} – ${fmtDate(edge.last_seen)}`}
      />
      {edge.source_message_ids.length > 0 ? (
        <div className="mb-3">
          <div className="text-[11px] uppercase tracking-wide text-slate-400">
            Source messages ({edge.source_message_ids.length})
          </div>
          <ul className="mt-1 space-y-1">
            {edge.source_message_ids.slice(0, 10).map((id) => (
              <li
                key={id}
                className="break-all rounded bg-slate-100 px-2 py-1 font-mono text-[10px] text-slate-500"
              >
                {id}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
