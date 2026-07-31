import { useState } from "react";
import { createReturnDraft, describeError } from "../api/client";
import type { HandoffPackage, ReturnContext } from "../api/types";
import { navigate } from "../router";

const HANDOFF_BASE = "/app/handoff";

/**
 * Creator entry point (S34): create a RETURN handoff from an original published
 * coverage package. This is reciprocal — NOT "create a revised version". The
 * source mailbox is the coverer's own loaded mailbox; the recipient defaults to
 * the original covered employee. The original package only seeds scope.
 */
export function ReturnCreatePanel({
  mailboxId,
  initialOriginalId,
}: {
  mailboxId: string;
  /** When present (routed from the package you were handed via ?return_from=…), the
   * original id is carried automatically — no id to find or paste. */
  initialOriginalId?: string;
}) {
  const linked = !!initialOriginalId?.trim();
  const [originalId, setOriginalId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const create = async (id: string) => {
    const oid = id.trim();
    if (!oid) return;
    setBusy(true);
    setError(null);
    try {
      const pkg: HandoffPackage = await createReturnDraft(oid, { coverer_mailbox_id: mailboxId });
      navigate(`${HANDOFF_BASE}/${pkg.id}`);
    } catch (e) {
      setError(describeError(e).message);
    } finally {
      setBusy(false);
    }
  };

  // Linked mode: the original package is carried in from the recipient view. The id
  // is used only for the create call — it is NOT shown as user-facing copy.
  if (linked) {
    return (
      <section className="mt-4 rounded-md border border-brass bg-brass-soft p-4">
        <h3 className="text-sm font-semibold text-ink">Create return handoff from the package you were handed</h3>
        <p className="mt-1 text-xs text-muted">
          The original coverage package is linked — its coverage areas are picked up automatically.
          The return handoff is created from <strong>your own mailbox</strong> (loaded above) and
          sent back to the original employee. It is a reciprocal package, not a revised version.
        </p>
        <button
          type="button"
          className="mt-3 rounded-md bg-brass px-4 py-2 text-sm font-medium text-onbrass hover:bg-brass disabled:bg-brass-soft disabled:text-faint"
          onClick={() => create(initialOriginalId as string)}
          disabled={busy}
        >
          {busy ? "Creating…" : "Create return handoff"}
        </button>
        {error ? <p className="mt-2 text-xs text-danger">{error}</p> : null}
      </section>
    );
  }

  // Manual mode (dev/debug fallback): paste an original package id.
  return (
    <section className="mt-4 rounded-md border border-line bg-surface p-4">
      <h3 className="text-sm font-semibold text-ink">Create a return handoff</h3>
      <p className="mt-1 text-xs text-muted">
        Covered someone while they were away? A <strong>return handoff</strong> is created from
        <strong> your own mailbox</strong> and sent back to the original employee — it is a
        reciprocal package, not a revised version. The usual way to start one is the
        <em> “Create return handoff”</em> button on the package you were handed; you can also paste
        an original coverage package id here.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="flex flex-1 flex-col text-xs text-muted">
          Original coverage package id
          <input
            type="text"
            className="mt-1 rounded border border-line2 px-2 py-1 text-sm"
            value={originalId}
            onChange={(e) => setOriginalId(e.target.value)}
            placeholder="UUID of the published package you received"
          />
        </label>
        <button
          type="button"
          className="rounded-md border border-line2 bg-app2 px-4 py-2 text-sm font-medium text-ink hover:bg-app disabled:opacity-50"
          onClick={() => create(originalId)}
          disabled={busy || !originalId.trim()}
        >
          {busy ? "Creating…" : "Create return handoff"}
        </button>
      </div>
      {error ? <p className="mt-2 text-xs text-danger">{error}</p> : null}
    </section>
  );
}

/**
 * Return-mode framing banner shown above the creator review when the loaded
 * package is a return_delta. Explains the automatic seed, surfaces the carried
 * coverage areas compactly, and sets return-specific review copy.
 */
export function ReturnBanner({ ctx, pkg }: { ctx: ReturnContext | null; pkg: HandoffPackage }) {
  const who = ctx?.original_creator_email ?? "the original employee";
  const from = ctx?.return_date_from ?? null;
  const to = ctx?.return_date_to ?? null;
  return (
    <section className="mt-4 rounded-md border border-brass bg-brass-soft p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">Return handoff — what changed while {who} was away</h3>
        <span className="rounded-full bg-app2 px-2 py-0.5 text-[11px] font-medium text-muted">return</span>
      </div>
      <p className="mt-1 text-xs text-muted">
        Seeded from the original coverage package. Coverage areas were picked up automatically.
        Claims are generated from <strong>your mailbox only</strong>, inside the carried coverage
        scope. <em>Review what changed while {who} was away, and remove anything that should not
        travel back.</em>
      </p>

      {ctx ? (
        <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
          <div>
            <div className="text-[11px] uppercase tracking-wide text-faint">Carried coverage areas</div>
            {ctx.carried_area_labels.length > 0 ? (
              <div className="mt-1 flex flex-wrap gap-1">
                {ctx.carried_area_labels.map((a) => (
                  <span key={a} className="rounded-full bg-app2 px-2 py-0.5 text-[11px] text-ink">{a}</span>
                ))}
              </div>
            ) : (
              <div className="mt-1 text-faint">
                {ctx.resolved_person_count > 0
                  ? `${ctx.resolved_person_count} coverage contact(s) (by domain)`
                  : "date window only"}
              </div>
            )}
            <div className="mt-1 text-faint">
              {ctx.resolved_project_count > 0
                ? `Resolved to ${ctx.resolved_project_count} project(s) in your mailbox`
                : ctx.resolved_person_count > 0
                  ? "Resolved via coverage contacts (snapshot hints)"
                  : "No coverer-side match resolved — review scope"}
              {" · "}seed: {ctx.seed_method}
            </div>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wide text-faint">Coverage period</div>
            <div className="mt-1 text-ink">{from ?? "—"} → {to ?? "today"}</div>
            <div className="mt-2 text-[11px] uppercase tracking-wide text-faint">Sends back to</div>
            <div className="mt-1 text-ink">{ctx.suggested_recipient_email || who}</div>
          </div>
        </div>
      ) : null}

      {pkg.status === "draft" ? (
        <p className="mt-3 text-[11px] text-faint">
          Generate the return handoff to build the coverage-delta candidate, then review and publish it back.
        </p>
      ) : null}
    </section>
  );
}
