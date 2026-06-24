/**
 * Display-only cleanup for S9-materialized project labels.
 *
 * S9 derives labels from clustering keywords/domains, which yields technically
 * valid but demo-rough strings like "Email Govdelivery", "Account Https", or
 * "unknown · Sep 2021". This module makes them read better WITHOUT changing any
 * clustering semantics: nothing here is persisted and the underlying label,
 * confidence, and ordering from the API are untouched. It is deliberately
 * conservative — it never invents meaning, only removes obvious machine noise,
 * maps empty/"unknown" labels to a neutral "Uncategorized", and flags
 * low-confidence labels so the UI can present them honestly.
 */

/**
 * Projects below this clustering confidence are shown with a muted
 * "low confidence" treatment. Mirrors the default per-tenant display threshold
 * (AGENTS.md invariant #4) so the UI hint matches the backend's own bar.
 */
export const LOW_CONFIDENCE_THRESHOLD = 0.4;

/** Standalone tokens that carry no human meaning in a project name. */
const NOISE_TOKENS = new Set(["https", "http", "www", "html", "url"]);

export interface CleanedLabel {
  /** Human-facing label to render. */
  display: string;
  /** True when the original label had no usable name (empty / "unknown"). */
  uncategorized: boolean;
  /** True when clustering confidence is below the display threshold. */
  lowConfidence: boolean;
}

/**
 * Clean a project label for display.
 *
 * Rules (all conservative and reversible — original data is never mutated):
 *  - Collapse whitespace and strip surrounding "·" separators.
 *  - Drop standalone URL-scheme noise tokens (https/http/www/html/url).
 *  - If nothing meaningful remains, or the label is an "unknown …" placeholder,
 *    present it as "Uncategorized" and mark `uncategorized`.
 *  - Mark `lowConfidence` when confidence < LOW_CONFIDENCE_THRESHOLD; callers
 *    use this to add a muted badge rather than hiding the project.
 */
export function cleanProjectLabel(
  label: string,
  confidence: number,
): CleanedLabel {
  const lowConfidence = confidence < LOW_CONFIDENCE_THRESHOLD;

  const raw = (label ?? "").trim();

  // "unknown" / "unknown · Sep 2021" style placeholders -> Uncategorized.
  if (raw === "" || /^unknown\b/i.test(raw)) {
    return { display: "Uncategorized", uncategorized: true, lowConfidence };
  }

  // Tokenize, drop pure-noise tokens, and collapse separators/whitespace.
  const cleaned = raw
    .split(/\s+/)
    .filter((tok) => tok !== "·" && !NOISE_TOKENS.has(tok.toLowerCase()))
    .join(" ")
    .replace(/\s*·\s*/g, " · ")
    .trim();

  if (cleaned === "") {
    return { display: "Uncategorized", uncategorized: true, lowConfidence };
  }

  return { display: cleaned, uncategorized: false, lowConfidence };
}
