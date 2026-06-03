import { useMemo } from "react";
import type { Node } from "../api/types";
import { ROLE_COLORS, roleColor, roleLabel } from "../utils/roleColors";

interface RoleLegendProps {
  nodes: Node[];
  activeRoles: Set<string>;
  onToggle: (role: string) => void;
}

export function RoleLegend({ nodes, activeRoles, onToggle }: RoleLegendProps) {
  // Count contacts per role. Show every canonical role plus any unexpected
  // role values the API happened to return, so nothing is silently dropped.
  const counts = useMemo(() => {
    const map = new Map<string, number>();
    for (const role of Object.keys(ROLE_COLORS)) {
      map.set(role, 0);
    }
    for (const node of nodes) {
      map.set(node.role, (map.get(node.role) ?? 0) + 1);
    }
    return map;
  }, [nodes]);

  return (
    <div className="flex flex-wrap items-center gap-2">
      {[...counts.entries()].map(([role, count]) => {
        const active = activeRoles.has(role);
        return (
          <button
            key={role}
            type="button"
            onClick={() => onToggle(role)}
            aria-pressed={active}
            className={`flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium transition ${
              active
                ? "border-slate-300 bg-white text-slate-700"
                : "border-slate-200 bg-slate-100 text-slate-400"
            }`}
            title={active ? "Click to hide" : "Click to show"}
          >
            <span
              className="inline-block h-3 w-3 rounded-full"
              style={{
                backgroundColor: roleColor(role),
                opacity: active ? 1 : 0.35,
              }}
            />
            <span>{roleLabel(role)}</span>
            <span className="text-slate-400">({count})</span>
          </button>
        );
      })}
    </div>
  );
}
