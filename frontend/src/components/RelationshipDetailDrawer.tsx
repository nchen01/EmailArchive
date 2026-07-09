import { useEffect, useState } from "react";
import { ApiError, fetchSourceMessage } from "../api/client";
import type {
  RelationshipEdge,
  RelationshipNode,
  SourceMessageDetail,
} from "../api/types";
import { REL_TYPE_LABEL } from "../utils/relationshipMap";
import { formatShortDate } from "../utils/formatDate";
import { SourceDetailView } from "./SourceDetail";

export type RelSelection =
  | { kind: "node"; node: RelationshipNode }
  | { kind: "edge"; edge: RelationshipEdge };

interface DrawerProps {
  selection: RelSelection | null;
  /** id → display label for resolving endpoints / project ids. */
  nodeLabels: Record<string, string>;
  /** Mailbox to resolve clicked source-message citations against (S14.6). */
  mailboxId: string;
  onClose: () => void;
}

/**
 * Right drawer showing why two nodes are connected, or details of a node.
 * Evidence-forward and volume-honest: it states the relationship type, a plain
 * explanation, evidence count (explicitly "not importance"), shared projects /
 * threads, and message-id citations. Each cited message is now clickable and
 * resolves to the same safe source-message detail used by Cover-for-me (S14.6);
 * it never shows raw bodies or sensitive content (the backend excludes sensitive
 * threads from derivation, and the detail endpoint 404s any excluded message).
 */
export function RelationshipDetailDrawer({
  selection,
  nodeLabels,
  mailboxId,
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
          <EdgeDetail
            edge={selection.edge}
            labelOf={labelOf}
            mailboxId={mailboxId}
          />
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

/**
 * Fetches and renders one clicked source-message citation. Fetches on mount and
 * whenever the header changes. A 404 (missing / wrong-mailbox / sensitivity-
 * excluded — indistinguishable by design) renders a neutral "detail not
 * available" note, never an inference that a sensitive message exists.
 */
function SourceMessageInspector({
  mailboxId,
  header,
  onBack,
}: {
  mailboxId: string;
  header: string;
  onBack: () => void;
}) {
  const [detail, setDetail] = useState<SourceMessageDetail | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "unavailable" | "error">(
    "loading",
  );

  useEffect(() => {
    let cancelled = false;
    setState("loading");
    setDetail(null);
    fetchSourceMessage(mailboxId, header)
      .then((d) => {
        if (cancelled) return;
        setDetail(d);
        setState("ready");
      })
      .catch((err) => {
        if (cancelled) return;
        setState(err instanceof ApiError && err.kind === "not_found" ? "unavailable" : "error");
      });
    return () => {
      cancelled = true;
    };
  }, [mailboxId, header]);

  return (
    <div>
      <button
        type="button"
        onClick={onBack}
        className="mb-3 text-[11px] font-medium text-slate-500 hover:text-slate-700"
      >
        ← Back to relationship
      </button>
      {state === "loading" ? (
        <p className="text-sm text-slate-500">Loading message detail…</p>
      ) : state === "ready" && detail ? (
        <SourceDetailView source={detail} />
      ) : state === "unavailable" ? (
        <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
          Detail not available for this message. It may be excluded or no longer
          present. The relationship still references it by the ID below.
          <div className="mt-2 break-all rounded bg-slate-100 px-2 py-1 font-mono text-[11px] text-slate-500">
            {header}
          </div>
        </div>
      ) : (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700">
          Couldn't load message detail. Try again.
        </div>
      )}
    </div>
  );
}

function EdgeDetail({
  edge,
  labelOf,
  mailboxId,
}: {
  edge: RelationshipEdge;
  labelOf: (id: string) => string;
  mailboxId: string;
}) {
  const [inspecting, setInspecting] = useState<string | null>(null);

  if (inspecting) {
    return (
      <SourceMessageInspector
        mailboxId={mailboxId}
        header={inspecting}
        onBack={() => setInspecting(null)}
      />
    );
  }

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
        value={`${formatShortDate(edge.first_seen)} – ${formatShortDate(edge.last_seen)}`}
      />
      {edge.source_message_ids.length > 0 ? (
        <div className="mb-3">
          <div className="text-[11px] uppercase tracking-wide text-slate-400">
            Source messages ({edge.source_message_ids.length})
          </div>
          <ul className="mt-1 space-y-1">
            {edge.source_message_ids.slice(0, 10).map((id) => (
              <li key={id}>
                <button
                  type="button"
                  onClick={() => setInspecting(id)}
                  title="Inspect this source message"
                  className="w-full break-all rounded bg-slate-100 px-2 py-1 text-left font-mono text-[10px] text-slate-500 hover:bg-slate-200 hover:text-slate-700"
                >
                  {id}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
