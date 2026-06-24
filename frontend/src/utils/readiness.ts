import type { PreflightCheck } from "../api/types";

/**
 * Shared readiness derivation (S12). Turns loaded counts + /api/preflight checks
 * into the five demo-readiness indicators used by the header health dot, the
 * Overview screen, and the readiness strip. Centralized so all three agree.
 */

export type Indicator = "ok" | "warn" | "bad" | "unknown";

export interface ReadinessItem {
  key: string;
  label: string;
  indicator: Indicator;
  hint: string;
}

function countIndicator(n: number | null): Indicator {
  if (n === null) return "unknown";
  return n > 0 ? "ok" : "warn";
}

function checkIndicator(c: PreflightCheck | undefined): Indicator {
  if (!c) return "unknown";
  switch (c.status) {
    case "pass":
      return "ok";
    case "warn":
      return "warn";
    case "fail":
      return "bad";
    default:
      return "unknown";
  }
}

export interface ReadinessInputs {
  checks: Record<string, PreflightCheck> | null;
  failed: boolean;
  contactCount: number | null;
  projectCount: number | null;
}

export function computeReadinessItems({
  checks,
  failed,
  contactCount,
  projectCount,
}: ReadinessInputs): ReadinessItem[] {
  const embeddings = checks?.embeddings;
  const embedClient = checks?.embed_client;
  const voyageKey = checks?.voyage_api_key;
  const anthropicKey = checks?.anthropic_api_key;

  // Retrieval is "available" only when the embed client constructs AND the
  // mailbox has embeddings; a missing key or missing embeddings is a soft warn.
  let retrieval: Indicator;
  if (failed) {
    retrieval = "unknown";
  } else if (
    checkIndicator(embedClient) === "ok" &&
    checkIndicator(embeddings) === "ok"
  ) {
    retrieval = "ok";
  } else if (
    checkIndicator(voyageKey) === "bad" ||
    checkIndicator(embeddings) === "bad"
  ) {
    retrieval = "warn"; // disabled but expected (no key / no embeddings)
  } else {
    retrieval = checkIndicator(embedClient);
  }

  return [
    {
      key: "contacts",
      label: "Contacts",
      indicator: countIndicator(contactCount),
      hint:
        contactCount === null
          ? "Contact count not loaded yet"
          : `${contactCount} contact(s) in the network map`,
    },
    {
      key: "projects",
      label: "Projects",
      indicator: countIndicator(projectCount),
      hint:
        projectCount === null
          ? "Project count not loaded yet"
          : `${projectCount} materialized project(s)`,
    },
    {
      key: "embeddings",
      label: "Embeddings",
      indicator: failed ? "unknown" : checkIndicator(embeddings),
      hint: embeddings?.message ?? "Embedding status unknown",
    },
    {
      key: "retrieval",
      label: "Retrieval",
      indicator: retrieval,
      hint: embedClient?.message ?? "Retrieval status unknown",
    },
    {
      key: "synthesis",
      label: "Synthesis",
      indicator: failed ? "unknown" : checkIndicator(anthropicKey),
      hint: anthropicKey?.message ?? "Synthesis (Anthropic key) status unknown",
    },
  ];
}

/** Worst-of summary for a single header dot: bad > warn > unknown > ok. */
export function overallIndicator(items: ReadinessItem[]): Indicator {
  if (items.some((i) => i.indicator === "bad")) return "bad";
  if (items.some((i) => i.indicator === "warn")) return "warn";
  if (items.some((i) => i.indicator === "unknown")) return "unknown";
  return "ok";
}

export const INDICATOR_DOT_CLASS: Record<Indicator, string> = {
  ok: "readiness-dot readiness-ok",
  warn: "readiness-dot readiness-warn",
  bad: "readiness-dot readiness-bad",
  unknown: "readiness-dot readiness-unknown",
};
