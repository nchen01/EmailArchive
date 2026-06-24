import { useDemoReadiness } from "../hooks/useDemoReadiness";
import type { PreflightCheck } from "../api/types";

interface DemoReadinessStripProps {
  mailboxId: string;
  /** Loaded contact count (network map), or null while unknown/loading. */
  contactCount: number | null;
  /** Loaded project count, or null while unknown/loading. */
  projectCount: number | null;
}

type Indicator = "ok" | "warn" | "bad" | "unknown";

const DOT_CLASS: Record<Indicator, string> = {
  ok: "readiness-dot readiness-ok",
  warn: "readiness-dot readiness-warn",
  bad: "readiness-dot readiness-bad",
  unknown: "readiness-dot readiness-unknown",
};

function countIndicator(n: number | null): Indicator {
  if (n === null) return "unknown";
  return n > 0 ? "ok" : "warn";
}

/** Map a preflight check's status to a strip indicator. */
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

/**
 * Unobtrusive status strip (S11) showing whether the loaded mailbox is
 * demo-ready: contacts, projects, embeddings, retrieval, and synthesis. Contacts
 * and projects come from data the app already loaded; the rest come from
 * /api/preflight. It never blocks the app — while preflight is loading or if it
 * fails, the dependent dots simply read "unknown".
 */
export function DemoReadinessStrip({
  mailboxId,
  contactCount,
  projectCount,
}: DemoReadinessStripProps) {
  const { checks, loading, failed } = useDemoReadiness(mailboxId || null);

  const embeddings = checks?.embeddings;
  const embedClient = checks?.embed_client;
  const voyageKey = checks?.voyage_api_key;
  const anthropicKey = checks?.anthropic_api_key;

  // Retrieval is "available" only when the embed client constructs AND the
  // mailbox has embeddings; a missing key or missing embeddings is a soft warn.
  let retrieval: Indicator;
  if (failed) {
    retrieval = "unknown";
  } else if (checkIndicator(embedClient) === "ok" && checkIndicator(embeddings) === "ok") {
    retrieval = "ok";
  } else if (checkIndicator(voyageKey) === "bad" || checkIndicator(embeddings) === "bad") {
    retrieval = "warn"; // disabled but expected (no key / no embeddings)
  } else {
    retrieval = checkIndicator(embedClient);
  }

  const items: { label: string; indicator: Indicator; hint: string }[] = [
    {
      label: "Contacts",
      indicator: countIndicator(contactCount),
      hint:
        contactCount === null
          ? "Contact count not loaded yet"
          : `${contactCount} contact(s) in the network map`,
    },
    {
      label: "Projects",
      indicator: countIndicator(projectCount),
      hint:
        projectCount === null
          ? "Project count not loaded yet"
          : `${projectCount} materialized project(s)`,
    },
    {
      label: "Embeddings",
      indicator: failed ? "unknown" : checkIndicator(embeddings),
      hint: embeddings?.message ?? "Embedding status unknown",
    },
    {
      label: "Retrieval",
      indicator: retrieval,
      hint: embedClient?.message ?? "Retrieval status unknown",
    },
    {
      label: "Synthesis",
      indicator: failed ? "unknown" : checkIndicator(anthropicKey),
      hint: anthropicKey?.message ?? "Synthesis (Anthropic key) status unknown",
    },
  ];

  return (
    <div className="readiness-strip" role="status" aria-label="Demo readiness">
      <span className="readiness-title">
        Readiness{loading ? " · checking…" : ""}
      </span>
      {items.map((it) => (
        <span key={it.label} className="readiness-item" title={it.hint}>
          <span className={DOT_CLASS[it.indicator]} aria-hidden="true" />
          {it.label}
        </span>
      ))}
      {failed ? (
        <span className="readiness-item readiness-muted" title="Preflight unavailable">
          (backend status unavailable)
        </span>
      ) : null}
    </div>
  );
}
