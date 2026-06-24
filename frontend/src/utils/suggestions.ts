import type { ProjectSummary } from "../api/types";
import { cleanProjectLabel } from "./projectLabels";

/**
 * Build Cover-for-me suggested questions (S12).
 *
 * Each suggestion is structured: `label` is the polished, display-friendly text
 * (built from the cleaned project label) and `query` is what actually gets sent
 * to Cover-for-me. The query MUST use the raw project label, because the backend
 * routes by matching the raw `Project.label`; if we sent the cleaned display
 * text (e.g. "Account" after stripping "Https") it could fail to route even
 * though it came from a real project.
 *
 * Project suggestions are preferred (they resolve against real data) but
 * uncategorized / "unknown" labels are skipped — their raw label will not route
 * reliably. Generic prompts (label === query) backfill. Capped at four.
 */

export interface Suggestion {
  /** Display-friendly chip text. */
  label: string;
  /** Exact string sent to Cover-for-me (raw label for projects). */
  query: string;
}

const GENERIC_SUGGESTIONS: Suggestion[] = [
  { label: "What work appears unresolved?", query: "What work appears unresolved?" },
  {
    label: "Who should I ask about the latest incident?",
    query: "Who should I ask about the latest incident?",
  },
];

export function buildSuggestions(projects: ProjectSummary[]): Suggestion[] {
  const projectSuggestions: Suggestion[] = [];
  for (const p of projects) {
    const { display, uncategorized } = cleanProjectLabel(p.label, p.confidence);
    // Skip labels we cannot route reliably (uncategorized / unknown placeholder).
    if (uncategorized) continue;
    projectSuggestions.push({
      label: `What's the state of ${display}?`,
      query: `What's the state of ${p.label}?`, // raw label → backend routing
    });
    if (projectSuggestions.length >= 2) break;
  }
  return [...projectSuggestions, ...GENERIC_SUGGESTIONS].slice(0, 4);
}
