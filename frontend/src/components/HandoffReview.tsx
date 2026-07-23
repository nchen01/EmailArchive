import { useEffect, useState } from "react";
import {
  createHandoff,
  describeError,
  generateHandoff,
  getHandoff,
  publishHandoff,
  revokeHandoff,
  updateHandoffScope,
} from "../api/client";
import type {
  HandoffEvidence,
  HandoffPackage,
  HandoffScopeData,
  PublishResponse,
} from "../api/types";
import { navigate, usePathname } from "../router";

const HANDOFF_BASE = "/app/handoff";

/**
 * Creator scope-review + publish surface (S17.4 review, S17.7 publish).
 *
 * The covered employee inspects and prunes a candidate handoff package built from
 * their OWN mailbox, then publishes it to a single recipient. This is deliberately
 * closer to the old mailbox-review UX (own data), and is NOT the recipient package
 * view (that read-only view lives at /handoff/recipient, S17.6). Publishing here
 * (PublishPanel) freezes the package and mints the one-time recipient link; the
 * raw capability code is held only in transient state and never persisted/logged.
 *
 * No frontend test runner exists in this repo (see docs/s15-verification-matrix.md);
 * verified by `npm run build` + the manual demo in the S17.7 request.
 */
const REASONS = ["vacation", "leave", "transfer", "delegation", "other"];

const KIND_LABEL: Record<string, string> = {
  open_loop: "Open loops",
  decision: "Decisions",
  blocker: "Blockers",
  project_state: "Project state",
  briefing: "Briefing",
  person_note: "People notes",
};
const KIND_ORDER = ["briefing", "project_state", "open_loop", "decision", "blocker", "person_note"];

const EXCLUSION_LABEL: Record<string, string> = {
  sensitivity: "excluded by sensitivity policy",
  user_removed: "removed by you",
  policy_removed: "removed by policy",
  duplicate: "duplicates",
  low_confidence: "low-confidence",
};

function fmtDate(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/** The COMPLETE current scope as a replace-like PATCH body (see ScopeRequestBody). */
function scopeBody(pkg: HandoffPackage): HandoffScopeData {
  return {
    date_from: pkg.scope.date_from,
    date_to: pkg.scope.date_to,
    included_project_ids: pkg.scope.included_project_ids,
    included_person_ids: pkg.scope.included_person_ids,
    included_thread_ids: pkg.scope.included_thread_ids,
    excluded_thread_ids: pkg.scope.excluded_thread_ids,
    excluded_message_id_headers: pkg.scope.excluded_message_id_headers,
    allowed_domains: pkg.scope.allowed_domains,
    keyword_filters: pkg.scope.keyword_filters,
  };
}

export function HandoffReview({ mailboxId }: { mailboxId: string }) {
  const pathname = usePathname();
  // The URL is the source of truth for which package is open, so a package
  // survives tab switches and refreshes (Finding 1): /app/handoff/<id>.
  const routeId = pathname.startsWith(`${HANDOFF_BASE}/`)
    ? decodeURIComponent(pathname.slice(HANDOFF_BASE.length + 1))
    : null;

  const [pkg, setPkg] = useState<HandoffPackage | null>(null);
  const [reason, setReason] = useState("vacation");
  const [title, setTitle] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState<
    null | "create" | "scope" | "generate" | "remove" | "publish" | "revoke"
  >(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // Transient publish result: holds the ONE-TIME capability code / share fragment
  // in React state ONLY, for the lifetime of this view. Never stored, never
  // logged. Cleared when the open package changes (see the reset effect below),
  // so a refresh cannot recover it.
  const [share, setShare] = useState<PublishResponse | null>(null);

  // Load / validate the package named in the URL for the current mailbox.
  // Runs on route or mailbox change; self-heals a route that points at a
  // missing package or another mailbox's package by returning to the base route.
  useEffect(() => {
    let cancelled = false;
    if (!routeId) {
      setPkg(null);
      return;
    }
    if (pkg && pkg.id === routeId && pkg.mailbox_id === mailboxId) return; // already loaded
    setError(null);
    setLoading(true);
    getHandoff(routeId)
      .then((data) => {
        if (cancelled) return;
        if (data.mailbox_id !== mailboxId) {
          setPkg(null);
          navigate(HANDOFF_BASE); // belongs to a different mailbox — drop safely
        } else {
          setPkg(data);
        }
      })
      .catch(() => {
        if (cancelled) return;
        setPkg(null);
        navigate(HANDOFF_BASE); // gone/invalid — back to the create surface
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeId, mailboxId, pkg?.id]);

  // Sync inputs to the loaded package, or reset the create form when none. Keyed
  // on pkg.id so it does NOT run on a same-package status change (e.g. publish),
  // which lets the transient share result survive the publish render — but a
  // different package / "start over" clears the one-time code, so it can never
  // be recovered after navigating away or refreshing.
  useEffect(() => {
    setShare(null);
    if (pkg) {
      setFrom(pkg.scope.date_from ?? "");
      setTo(pkg.scope.date_to ?? "");
    } else {
      setReason("vacation");
      setTitle("");
      setFrom("");
      setTo("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pkg?.id]);

  const run = async (kind: typeof busy, fn: () => Promise<HandoffPackage>) => {
    setBusy(kind);
    setError(null);
    try {
      setPkg(await fn());
    } catch (e) {
      setError(describeError(e).message);
    } finally {
      setBusy(null);
    }
  };

  const create = () =>
    run("create", async () => {
      const p = await createHandoff(mailboxId, { reason, title });
      navigate(`${HANDOFF_BASE}/${p.id}`); // deep-linkable + survives refresh
      return p;
    });
  const saveScope = () =>
    pkg &&
    run("scope", () =>
      updateHandoffScope(pkg.id, { ...scopeBody(pkg), date_from: from || null, date_to: to || null }),
    );
  const generate = () => pkg && run("generate", () => generateHandoff(pkg.id));

  const removeEvidence = (header: string) =>
    pkg &&
    run("remove", async () => {
      const excluded = [...pkg.scope.excluded_message_id_headers, header];
      await updateHandoffScope(pkg.id, { ...scopeBody(pkg), excluded_message_id_headers: excluded });
      return generateHandoff(pkg.id); // prune + rebuild in one action
    });

  // Publish freezes the package and returns the one-time code exactly once; we
  // hold the result in transient state (setShare) and never persist/log it.
  const publish = async (recipientEmail: string, expiresInDays: number | null) => {
    if (!pkg) return;
    setBusy("publish");
    setError(null);
    try {
      const resp = await publishHandoff(pkg.id, {
        recipient_email: recipientEmail,
        ...(expiresInDays ? { expires_in_days: expiresInDays } : {}),
      });
      setPkg(resp.package); // same id, status now "published" → share survives
      setShare(resp);
    } catch (e) {
      setError(describeError(e).message);
    } finally {
      setBusy(null);
    }
  };

  const revoke = async () => {
    if (!pkg) return;
    if (
      !window.confirm(
        "Revoke this handoff? The recipient's access is blocked immediately and " +
          "cannot be restored from here.",
      )
    )
      return;
    setBusy("revoke");
    setError(null);
    try {
      const updated = await revokeHandoff(pkg.id);
      setPkg(updated);
      setShare(null); // the link is dead now; stop showing it
    } catch (e) {
      setError(describeError(e).message);
    } finally {
      setBusy(null);
    }
  };

  const copyId = (value: string) => {
    navigator.clipboard?.writeText(value).catch(() => window.prompt("Copy this Message-ID:", value));
  };

  const claimsByKind = (kind: string) => (pkg?.claims ?? []).filter((c) => c.kind === kind);
  const excludedHeaders = new Set(pkg?.scope.excluded_message_id_headers ?? []);
  // Only draft/generated packages are editable. Once published (or revoked) the
  // package is immutable: scope edits, regenerate, and evidence removal are all
  // disabled in the UI (§immutability). Backend also rejects these transitions.
  const mutable = !pkg || pkg.status === "draft" || pkg.status === "generated";

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8">
      <h2 className="text-lg font-semibold text-slate-800">Review handoff package</h2>
      <p className="mt-1 text-sm text-slate-500">
        Inspect and prune what a coverage handoff would reveal, from your own
        mailbox, <strong>before publishing</strong>. This is your review surface —
        not the recipient view. Sensitive and noise messages are excluded
        automatically; you can remove anything else. When it looks right,{" "}
        <strong>publish</strong> to freeze it and generate a one-time recipient
        link.
      </p>

      {error ? (
        <div className="status-failed mt-4" role="alert">
          {error}
        </div>
      ) : null}

      {/* ── Create (or loading a deep-linked package) ─────────────────────── */}
      {loading && !pkg ? (
        <p className="mt-6 text-sm text-slate-500">Loading handoff package…</p>
      ) : !pkg ? (
        <section className="mt-6 rounded-md border border-slate-200 bg-white p-4">
          <h3 className="text-sm font-semibold text-slate-700">Start a handoff</h3>
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <label className="flex flex-col text-xs text-slate-500">
              Reason
              <select
                className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              >
                {REASONS.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-1 flex-col text-xs text-slate-500">
              Title (optional)
              <input
                type="text"
                className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder='e.g. "Covering Atlas while I&apos;m out"'
              />
            </label>
            <button
              type="button"
              className="rounded-md bg-slate-800 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:bg-slate-300"
              onClick={create}
              disabled={busy !== null}
            >
              {busy === "create" ? "Creating…" : "Create draft"}
            </button>
          </div>
        </section>
      ) : (
        <>
          {/* ── Package meta + scope ─────────────────────────────────────── */}
          <section className="mt-6 rounded-md border border-slate-200 bg-white p-4">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-semibold text-slate-800">
                  {pkg.title || "(untitled handoff)"}
                </div>
                <div className="text-xs text-slate-500">
                  reason: {pkg.reason} · status:{" "}
                  <span className="font-medium text-slate-700">{pkg.status}</span> · v{pkg.version}
                </div>
              </div>
              <button
                type="button"
                className="text-xs text-slate-400 hover:text-slate-600"
                onClick={() => navigate(HANDOFF_BASE)}
              >
                Start over
              </button>
            </div>

            {mutable ? (
              <div className="mt-3 flex flex-wrap items-end gap-3 border-t border-slate-100 pt-3">
                <label className="flex flex-col text-xs text-slate-500">
                  From
                  <input
                    type="date"
                    className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm"
                    value={from}
                    onChange={(e) => setFrom(e.target.value)}
                  />
                </label>
                <label className="flex flex-col text-xs text-slate-500">
                  To
                  <input
                    type="date"
                    className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm"
                    value={to}
                    onChange={(e) => setTo(e.target.value)}
                  />
                </label>
                <button
                  type="button"
                  className="rounded-md border border-slate-300 px-3 py-2 text-sm hover:bg-slate-50 disabled:opacity-50"
                  onClick={saveScope}
                  disabled={busy !== null}
                >
                  {busy === "scope" ? "Saving…" : "Save scope"}
                </button>
                <button
                  type="button"
                  className="rounded-md bg-slate-800 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:bg-slate-300"
                  onClick={generate}
                  disabled={busy !== null}
                >
                  {busy === "generate" || busy === "remove" ? "Generating…" : "Generate package"}
                </button>
              </div>
            ) : (
              <div className="mt-3 border-t border-slate-100 pt-3 text-xs text-slate-400">
                This package is {pkg.status} and frozen — scope, regeneration, and
                evidence are locked.
              </div>
            )}
            {(pkg.scope.included_project_ids.length > 0 ||
              pkg.scope.included_person_ids.length > 0 ||
              pkg.scope.included_thread_ids.length > 0) && (
              <div className="mt-2 text-xs text-slate-400">
                Scoped to {pkg.scope.included_project_ids.length} project(s),{" "}
                {pkg.scope.included_person_ids.length} person(s),{" "}
                {pkg.scope.included_thread_ids.length} thread(s).
              </div>
            )}
          </section>

          {/* ── Generated content (kept visible read-only after publish) ──── */}
          {pkg.status !== "draft" ? (
            <>
              <ExclusionSummary counts={pkg.exclusion_counts} />

              <section className="mt-4">
                <h3 className="text-sm font-semibold text-slate-700">
                  Claims ({pkg.claims.length})
                </h3>
                {pkg.claims.length === 0 ? (
                  <p className="mt-1 text-sm text-slate-400">
                    No package claims were generated from the current scope. Try a
                    wider scope, or run against a mailbox with extracted events.
                  </p>
                ) : (
                  KIND_ORDER.filter((k) => claimsByKind(k).length > 0).map((kind) => (
                    <div key={kind} className="mt-3">
                      <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                        {KIND_LABEL[kind] ?? kind}
                      </div>
                      <ul className="mt-1 space-y-2">
                        {claimsByKind(kind).map((c) => (
                          <li
                            key={c.id}
                            className="rounded-md border border-slate-100 bg-white px-3 py-2 text-sm"
                          >
                            <div className="text-slate-800">{c.text}</div>
                            <div className="mt-1 flex flex-wrap gap-1">
                              {c.source_message_id_headers.map((h) => (
                                <span
                                  key={h}
                                  className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-500"
                                  title="Cited source message"
                                >
                                  {h}
                                </span>
                              ))}
                            </div>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))
                )}
              </section>

              <section className="mt-6">
                <h3 className="text-sm font-semibold text-slate-700">
                  Evidence ({pkg.evidence.length})
                </h3>
                <p className="text-xs text-slate-400">
                  {mutable
                    ? "Snapshotted safe message content. Remove anything that should not travel with the handoff; a claim left without evidence disappears on regenerate."
                    : "Snapshotted safe message content, frozen at publish. This is exactly what the recipient can read."}
                </p>
                <ul className="mt-2 space-y-3">
                  {pkg.evidence.map((e) => (
                    <EvidenceCard
                      key={e.message_id_header}
                      ev={e}
                      removing={excludedHeaders.has(e.message_id_header)}
                      onRemove={() => removeEvidence(e.message_id_header)}
                      onCopy={() => copyId(e.message_id_header)}
                      disabled={busy !== null}
                      canRemove={mutable}
                    />
                  ))}
                  {pkg.evidence.length === 0 ? (
                    <li className="text-sm text-slate-400">No evidence in scope.</li>
                  ) : null}
                </ul>
              </section>

              <PublishPanel
                pkg={pkg}
                share={share}
                busy={busy}
                onPublish={publish}
                onRevoke={revoke}
              />
            </>
          ) : (
            <p className="mt-4 text-sm text-slate-500">
              Set a scope (optional) and click <strong>Generate package</strong> to
              build the candidate for review.
            </p>
          )}
        </>
      )}
    </div>
  );
}

function ExclusionSummary({ counts }: { counts: Record<string, number> }) {
  const parts = Object.entries(counts)
    .filter(([, n]) => n > 0)
    .map(([type, n]) => `${n} ${EXCLUSION_LABEL[type] ?? type}`);
  if (parts.length === 0) return null;
  return (
    <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
      Withheld from this package (visible to you only, never to the recipient):{" "}
      {parts.join(" · ")}.
    </div>
  );
}

function EvidenceCard({
  ev,
  removing,
  onRemove,
  onCopy,
  disabled,
  canRemove,
}: {
  ev: HandoffEvidence;
  removing: boolean;
  onRemove: () => void;
  onCopy: () => void;
  disabled: boolean;
  canRemove: boolean;
}) {
  const from = [ev.sender_display, ev.sender_domain].filter(Boolean).join(" · ");
  return (
    <li className="rounded-md border border-slate-200 bg-white p-3 text-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-semibold text-slate-900">{ev.subject || "(no subject)"}</div>
          <div className="text-xs text-slate-500">
            {from || "—"} · {fmtDate(ev.date)}
          </div>
        </div>
        {canRemove ? (
          <button
            type="button"
            className="shrink-0 rounded border border-slate-200 px-2 py-1 text-[11px] font-medium text-red-600 hover:bg-red-50 disabled:opacity-50"
            onClick={onRemove}
            disabled={disabled}
            title="Exclude this message and regenerate"
          >
            {removing ? "Removing…" : "Remove"}
          </button>
        ) : null}
      </div>
      {ev.body_snapshot ? (
        <p className="mt-2 whitespace-pre-wrap rounded bg-slate-50 px-2 py-1.5 text-[13px] text-slate-700">
          {ev.body_snapshot}
        </p>
      ) : null}
      <div className="mt-1 flex items-center gap-2">
        <span className="break-all rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-500">
          {ev.message_id_header}
        </span>
        <button
          type="button"
          className="rounded border border-slate-200 px-2 py-0.5 text-[10px] text-slate-500 hover:bg-slate-100"
          onClick={onCopy}
        >
          Copy ID
        </button>
      </div>
    </li>
  );
}

/**
 * Publish + share-link panel (S17.7). Three states driven by package status:
 *  - generated → the publish form (recipient email + expiry, default 30 days).
 *  - published → the success/share state. If `share` (the transient publish
 *    result) is present, the ONE-TIME recipient link is shown as a copyable field
 *    with a "shown once" warning. On a refresh `share` is gone (never persisted),
 *    so we honestly say the link cannot be recovered.
 *  - revoked  → a terminal notice.
 *
 * The creator is deliberately given NO "open the link" affordance: the code is
 * one-time, so opening it here would consume it before the recipient can. Copy is
 * the only action. The raw capability code lives ONLY inside `share` (transient
 * React state) — never written to storage, never logged, and only ever placed in
 * the URL *fragment* of the copyable link.
 */
function PublishPanel({
  pkg,
  share,
  busy,
  onPublish,
  onRevoke,
}: {
  pkg: HandoffPackage;
  share: PublishResponse | null;
  busy: string | null;
  onPublish: (recipientEmail: string, expiresInDays: number | null) => void;
  onRevoke: () => void;
}) {
  const [email, setEmail] = useState("");
  const [days, setDays] = useState("30");
  const [copied, setCopied] = useState(false);

  if (pkg.status === "revoked") {
    return (
      <section className="mt-6 rounded-md border border-red-200 bg-red-50 p-4">
        <h3 className="text-sm font-semibold text-red-800">Access revoked</h3>
        <p className="mt-1 text-sm text-red-700">
          This handoff was revoked{pkg.revoked_at ? ` on ${fmtDate(pkg.revoked_at)}` : ""}. The
          recipient can no longer open it, and a revoked package cannot be
          re-shared from here.
        </p>
      </section>
    );
  }

  if (pkg.status === "published") {
    const shareUrl = share
      ? `${window.location.origin}/handoff/recipient${share.share_fragment}`
      : null;
    const copy = () => {
      if (!shareUrl) return;
      navigator.clipboard
        ?.writeText(shareUrl)
        .then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        })
        .catch(() => window.prompt("Copy this recipient link:", shareUrl));
    };
    return (
      <section className="mt-6 rounded-md border border-emerald-300 bg-emerald-50 p-4">
        <h3 className="text-sm font-semibold text-emerald-900">Package published</h3>
        <div className="mt-1 text-xs text-emerald-800">
          {share ? <>Recipient: {share.recipient_email} · </> : null}
          Access expires {fmtDate(pkg.expires_at ?? share?.expires_at ?? "")}
        </div>

        {share && shareUrl ? (
          <div className="mt-3">
            <label className="block text-xs font-medium text-emerald-900">
              Recipient share link
              <input
                type="text"
                readOnly
                value={shareUrl}
                onFocus={(e) => e.currentTarget.select()}
                className="mt-1 w-full rounded border border-emerald-300 bg-white px-2 py-1 font-mono text-xs text-slate-700"
              />
            </label>
            <div className="mt-2 rounded bg-amber-100 px-2 py-1.5 text-xs text-amber-900">
              <strong>This link is shown once.</strong> Copy it now and store it
              securely — the code cannot be recovered later, not even by you. The
              code is one-time: it is consumed the first time it is opened, so do
              not open it yourself before sending it to the recipient.
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button
                type="button"
                className="rounded-md bg-emerald-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-600"
                onClick={copy}
              >
                {copied ? "Copied!" : "Copy link"}
              </button>
            </div>
          </div>
        ) : (
          <p className="mt-3 text-xs text-emerald-800">
            The share link was shown once, when this package was published, and
            cannot be recovered from the server. If a new link is needed, revoke
            this package (issuing a fresh version to re-share is a later step).
          </p>
        )}

        <div className="mt-4 border-t border-emerald-200 pt-3">
          <button
            type="button"
            className="text-xs font-medium text-red-600 hover:text-red-700 disabled:opacity-50"
            onClick={onRevoke}
            disabled={busy !== null}
          >
            {busy === "revoke" ? "Revoking…" : "Revoke access"}
          </button>
        </div>
      </section>
    );
  }

  // status === "generated": the publish form.
  const noEvidence = pkg.evidence.length === 0;
  const parsedDays = parseInt(days, 10);
  const submit = () =>
    onPublish(email.trim(), Number.isFinite(parsedDays) && parsedDays > 0 ? parsedDays : null);

  return (
    <section className="mt-6 rounded-md border border-indigo-300 bg-indigo-50 p-4">
      <h3 className="text-sm font-semibold text-indigo-900">Publish package</h3>
      <p className="mt-1 text-xs text-indigo-800">
        Publishing freezes this package and generates a one-time recipient link.
        After publishing you can no longer edit scope, regenerate, or remove
        evidence.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="flex flex-1 flex-col text-xs text-indigo-900">
          Recipient email
          <input
            type="email"
            className="mt-1 rounded border border-indigo-300 bg-white px-2 py-1 text-sm text-slate-800"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="cover@company.com"
          />
        </label>
        <label className="flex w-28 flex-col text-xs text-indigo-900">
          Expires (days)
          <input
            type="number"
            min={1}
            max={365}
            className="mt-1 rounded border border-indigo-300 bg-white px-2 py-1 text-sm text-slate-800"
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
        </label>
        <button
          type="button"
          className="rounded-md bg-indigo-700 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-600 disabled:bg-indigo-300"
          onClick={submit}
          disabled={busy !== null || !email.trim() || noEvidence}
          title={noEvidence ? "Generate a package with evidence before publishing" : undefined}
        >
          {busy === "publish" ? "Publishing…" : "Publish package"}
        </button>
      </div>
      {noEvidence ? (
        <p className="mt-2 text-xs text-indigo-700">
          This package has no evidence yet — widen the scope and regenerate before
          publishing.
        </p>
      ) : null}
    </section>
  );
}
