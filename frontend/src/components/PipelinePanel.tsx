import { useRef, useState } from "react";
import {
  describeError,
  embedBackfill,
  enqueueEnrich,
  enqueueExtractEvents,
  enqueueMaterialize,
  getJob,
  isJobTerminal,
  type EmbedEstimate,
} from "../api/client";

/**
 * Minimal creator-side pipeline controls (S26). After Gmail ingest, the
 * post-ingest work runs as explicit background jobs, in order. This panel just
 * enqueues each and polls its status — no tokens/content are shown. It never runs
 * embeddings without an explicit cost confirm.
 */
type Step = "enrich" | "events" | "embed" | "materialize";

const NEXT_HINT =
  "After Gmail ingest, run these in order: 1) Enrichment → 2) Event extraction → " +
  "3) Embedding backfill → 4) Project materialization.";

export function PipelinePanel({ mailboxId }: { mailboxId: string }) {
  const [status, setStatus] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [estimate, setEstimate] = useState<EmbedEstimate | null>(null);
  const [busy, setBusy] = useState<Step | null>(null);
  const timers = useRef<Record<string, number>>({});

  const poll = (step: Step, jobId: string) => {
    getJob(jobId)
      .then((j) => {
        setStatus((s) => ({ ...s, [step]: j.status }));
        if (!isJobTerminal(j.status)) {
          timers.current[step] = window.setTimeout(() => poll(step, jobId), 1500);
        }
      })
      .catch((e) => setError(describeError(e).message));
  };

  const run = async (step: Step, fn: () => Promise<{ job_id: string; status: string }>) => {
    setError(null);
    setBusy(step);
    try {
      const job = await fn();
      setStatus((s) => ({ ...s, [step]: job.status }));
      poll(step, job.job_id);
    } catch (e) {
      setError(describeError(e).message);
    } finally {
      setBusy(null);
    }
  };

  const doEmbedEstimate = async () => {
    setError(null);
    setBusy("embed");
    try {
      const res = await embedBackfill(mailboxId, false);
      if ("needs_confirm" in res) setEstimate(res);
    } catch (e) {
      setError(describeError(e).message);
    } finally {
      setBusy(null);
    }
  };

  const confirmEmbed = async () => {
    setEstimate(null);
    await run("embed", async () => {
      const res = await embedBackfill(mailboxId, true);
      if ("job_id" in res) return res;
      throw new Error("unexpected estimate response");
    });
  };

  const Row = ({ step, label, onClick }: { step: Step; label: string; onClick: () => void }) => (
    <div className="flex items-center justify-between gap-3 border-t border-line py-2">
      <span className="text-sm text-ink">{label}</span>
      <span className="flex items-center gap-3">
        {status[step] ? (
          <span className="font-mono text-xs text-muted">{status[step]}</span>
        ) : null}
        <button
          type="button"
          className="rounded-md border border-line2 px-3 py-1 text-xs font-medium text-ink hover:bg-app2 disabled:opacity-50"
          onClick={onClick}
          disabled={busy !== null}
        >
          Run
        </button>
      </span>
    </div>
  );

  return (
    <section className="mt-6 rounded-md border border-line bg-surface p-4">
      <h2 className="text-sm font-semibold text-ink">Post-ingest pipeline</h2>
      <p className="mt-1 text-xs text-muted">{NEXT_HINT}</p>

      <Row step="enrich" label="1. Enrichment (people, relationships, roles)"
           onClick={() => run("enrich", () => enqueueEnrich(mailboxId))} />
      <Row step="events" label="2. Event extraction (needs an Anthropic key)"
           onClick={() => run("events", () => enqueueExtractEvents(mailboxId))} />

      <div className="flex items-center justify-between gap-3 border-t border-line py-2">
        <span className="text-sm text-ink">3. Embedding backfill (Voyage, cost-gated)</span>
        <span className="flex items-center gap-3">
          {status.embed ? <span className="font-mono text-xs text-muted">{status.embed}</span> : null}
          <button
            type="button"
            className="rounded-md border border-line2 px-3 py-1 text-xs font-medium text-ink hover:bg-app2 disabled:opacity-50"
            onClick={doEmbedEstimate}
            disabled={busy !== null}
          >
            Estimate
          </button>
        </span>
      </div>
      {estimate ? (
        <div className="mt-1 rounded-md border border-warn-line bg-warn-soft px-3 py-2 text-xs text-warn">
          Will embed <strong>{estimate.to_embed}</strong> messages (~{estimate.est_tokens.toLocaleString()} tokens,
          ~${estimate.est_cost_usd.toFixed(4)} via {estimate.model}). {estimate.already_embedded} already embedded.
          <div className="mt-2 flex gap-2">
            <button type="button" className="rounded-md bg-brass px-3 py-1 text-xs font-medium text-onbrass hover:bg-brass"
              onClick={confirmEmbed}>Confirm &amp; run</button>
            <button type="button" className="rounded-md border border-line2 px-3 py-1 text-xs text-ink hover:bg-app2"
              onClick={() => setEstimate(null)}>Cancel</button>
          </div>
        </div>
      ) : null}

      <Row step="materialize" label="4. Project materialization (needs embeddings first)"
           onClick={() => run("materialize", () => enqueueMaterialize(mailboxId))} />

      {error ? <p className="mt-2 text-xs text-danger">{error}</p> : null}
      <p className="mt-2 text-[11px] text-faint">
        Each step runs as a background job; a worker (scripts/run_worker.py) must be running to execute them.
      </p>
    </section>
  );
}
