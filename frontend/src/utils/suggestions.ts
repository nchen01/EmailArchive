import type { ProjectSummary } from "../api/types";
import { cleanProjectLabel } from "./projectLabels";

/**
 * Build Cover-for-me suggested questions (S12). Prefers questions about the
 * mailbox's own top projects so they actually resolve against real data, then
 * falls back to generic prompts. Shared by the Overview screen and the inline
 * Cover-for-me empty state so both offer the same starting points. Capped at four.
 */

const GENERIC_SUGGESTIONS = [
  "What work appears unresolved?",
  "Who should I ask about the latest incident?",
];

export function buildSuggestions(projects: ProjectSummary[]): string[] {
  const projectSuggestions = projects
    .slice(0, 2)
    .map(
      (p) =>
        `What's the state of ${cleanProjectLabel(p.label, p.confidence).display}?`,
    );
  return [...projectSuggestions, ...GENERIC_SUGGESTIONS].slice(0, 4);
}
