import { useEffect, useRef, useState } from "react";
import { getRecipientPackage, startRecipientSession } from "../api/client";
import type { RecipientClaim, RecipientPackage as RecipientPackageData } from "../api/types";

/**
 * Recipient handoff-package view (S17.6).
 *
 * This is the FIRST recipient-facing surface. It is a delivered, read-only
 * package — deliberately NOT the creator workspace and NOT a live mailbox
 * explorer. A coverage recipient opens a share link of the form
 * `/handoff/recipient#c=<capability_code>`; this component:
 *
 *   1. reads the one-time code from the URL *fragment* (never a path/query),
 *   2. immediately strips the fragment from the address bar + history so the raw
 *      code is not preserved in visible navigation,
 *   3. POSTs it to /api/handoff/recipient/session for a short-lived session token
 *      held in memory only (never localStorage/sessionStorage, never logged),
 *   4. reads the package via GET /api/handoff/recipient/package with that token,
 *   5. renders package-local claims + evidence + a constant privacy posture.
 *
 * Hard privacy invariants honored here (spec §8/§10): no mailbox id, no exclusion
 * counts, no Gmail/source/open_url affordance, no live-search suggestion, and no
 * signal about whether sensitive content existed. Every failure (invalid /
 * expired / revoked / already-consumed code, dead session, transport error)
 * collapses to ONE neutral "unavailable" state — no oracle about the cause.
 *
 * Refresh behavior: the session token lives only in React memory and the code is
 * one-time and already stripped from the URL, so a browser refresh drops the
 * session and lands on the neutral "open your link" state. The recipient must
 * re-open the original share link (which re-exchanges a fresh session) — this is
 * the intended MVP trade-off, documented in the S17.6 handoff.
 *
 * No frontend test runner exists in this repo (docs/s15-verification-matrix.md);
 * verified via `npm run build` + the manual demo in the S17.6 request.
 */

type Phase = "opening" | "loading" | "ready" | "unavailable" | "nolink";

const KIND_LABEL: Record<string, string> = {
  briefing: "Briefing",
  project_state: "Project state",
  open_loop: "Open loops",
  decision: "Decisions",
  blocker: "Blockers",
  person_note: "People notes",
};
const KIND_ORDER = ["briefing", "project_state", "open_loop", "decision", "blocker", "person_note"];

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function RecipientPackage() {
  const [phase, setPhase] = useState<Phase>("opening");
  const [pkg, setPkg] = useState<RecipientPackageData | null>(null);
  // Session token is intentionally NOT state and NOT storage — memory only.
  const sessionRef = useRef<string | null>(null);
  // Guard so the one-time code is exchanged exactly once, even under React
  // StrictMode's dev double-invoke of effects (a second exchange of a spent code
  // would 403 and wrongly clobber a good render).
  const startedRef = useRef(false);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    // Read the one-time code from the fragment, then strip it. replaceState (not
    // pushState) means Back does not resurface the code, and the address bar no
    // longer shows it. The code is never written to state or logged.
    const hash = window.location.hash.startsWith("#") ? window.location.hash.slice(1) : "";
    const code = new URLSearchParams(hash).get("c");
    if (window.location.hash) {
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
    }

    if (!code) {
      setPhase("nolink");
      return;
    }

    // The one-time exchange must run to completion once; we deliberately do NOT
    // cancel it on unmount (StrictMode simulates an unmount/remount on the same
    // instance, and cancelling would drop the only valid exchange).
    void (async () => {
      try {
        const session = await startRecipientSession(code);
        sessionRef.current = session.session_token;
        setPhase("loading");
        const data = await getRecipientPackage(session.session_token);
        setPkg(data);
        setPhase("ready");
      } catch {
        // Neutral collapse: invalid/expired/revoked/consumed/transport all look
        // identical to the recipient — no reason is ever surfaced.
        setPhase("unavailable");
      }
    })();
  }, []);

  if (phase === "opening" || phase === "loading") {
    return (
      <Shell>
        <div className="animate-pulse text-sm text-slate-500">
          {phase === "opening" ? "Opening your handoff package…" : "Loading package contents…"}
        </div>
      </Shell>
    );
  }

  if (phase === "nolink") {
    return (
      <Shell>
        <NeutralCard
          heading="Open your handoff link"
          body="This page opens from the secure link that was shared with you. Please open that original link again to view the handoff."
        />
      </Shell>
    );
  }

  if (phase === "unavailable" || !pkg) {
    return (
      <Shell>
        <NeutralCard
          heading="This handoff is no longer available"
          body="The link may have expired, been withdrawn, or already been opened. Ask the person who shared it to send you a new link."
        />
      </Shell>
    );
  }

  return (
    <Shell>
      <PackageDocument pkg={pkg} />
    </Shell>
  );
}

/** Full-height tinted backdrop that frames the package as a delivered document,
 * visually distinct from the white-on-default creator workspace. */
function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-full bg-slate-100">
      <div className="mx-auto w-full max-w-3xl px-4 py-10">{children}</div>
    </div>
  );
}

function NeutralCard({ heading, body }: { heading: string; body: string }) {
  return (
    <div className="mt-10 rounded-lg border border-slate-200 bg-white p-8 text-center shadow-sm">
      <h1 className="text-lg font-semibold text-slate-800">{heading}</h1>
      <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-slate-500">{body}</p>
    </div>
  );
}

function PackageDocument({ pkg }: { pkg: RecipientPackageData }) {
  const claimsByKind = (kind: string) => pkg.claims.filter((c) => c.kind === kind);
  const groupedKinds = KIND_ORDER.filter((k) => claimsByKind(k).length > 0);
  // Any claim kinds outside the known order still render, so nothing is dropped.
  const extraKinds = Array.from(new Set(pkg.claims.map((c) => c.kind))).filter(
    (k) => !KIND_ORDER.includes(k),
  );

  return (
    <article className="overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-slate-200">
      {/* Document header band — the delivered-package identity. */}
      <header className="bg-indigo-900 px-8 py-7 text-indigo-50">
        <div className="text-xs font-semibold uppercase tracking-widest text-indigo-300">
          Handoff package · Read-only
        </div>
        <h1 className="mt-2 text-2xl font-semibold leading-tight">
          {pkg.title || "Coverage handoff"}
        </h1>
        <div className="mt-3 text-sm text-indigo-200">
          Prepared for you by <span className="font-medium text-indigo-50">{pkg.creator_email}</span>
          {pkg.reason ? <> · {pkg.reason}</> : null}
        </div>
        <div className="mt-1 text-xs text-indigo-300">
          Shared {fmtDate(pkg.published_at)}
          {pkg.expires_at ? <> · access expires {fmtDate(pkg.expires_at)}</> : null}
        </div>
      </header>

      <div className="px-8 py-6">
        {/* Privacy posture — reassurance, constant, no counts/oracle. */}
        <aside className="rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
          <div className="font-medium">Scope-limited · Sensitive content excluded</div>
          <p className="mt-1 leading-relaxed text-emerald-800">{pkg.privacy_posture.note}</p>
        </aside>

        {/* Claims, grouped by kind. */}
        <section className="mt-8">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            What you need to know
          </h2>
          {pkg.claims.length === 0 ? (
            <p className="mt-2 text-sm text-slate-400">
              This handoff has no summary points.
            </p>
          ) : (
            [...groupedKinds, ...extraKinds].map((kind) => (
              <div key={kind} className="mt-5">
                <div className="text-xs font-semibold uppercase tracking-wide text-indigo-500">
                  {KIND_LABEL[kind] ?? kind}
                </div>
                <ul className="mt-2 space-y-2">
                  {claimsByKind(kind).map((c) => (
                    <ClaimRow key={c.id} claim={c} />
                  ))}
                </ul>
              </div>
            ))
          )}
        </section>

        {/* Cited evidence — snapshotted content only, no source/Gmail links. */}
        <section className="mt-9">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Supporting messages ({pkg.evidence.length})
          </h2>
          {pkg.evidence.length === 0 ? (
            <p className="mt-2 text-sm text-slate-400">No supporting messages were included.</p>
          ) : (
            <ul className="mt-3 space-y-3">
              {pkg.evidence.map((e) => (
                <li
                  key={e.message_id_header}
                  className="rounded-md border border-slate-200 bg-slate-50 p-4"
                >
                  <div className="text-sm font-medium text-slate-800">
                    {e.subject || "(no subject)"}
                  </div>
                  <div className="mt-0.5 text-xs text-slate-500">
                    {e.sender_display || "Unknown sender"}
                    {e.sender_domain ? <> · {e.sender_domain}</> : null}
                    {e.date ? <> · {fmtDate(e.date)}</> : null}
                  </div>
                  {e.body_snapshot ? (
                    <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-slate-700">
                      {e.body_snapshot}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </section>

        <footer className="mt-9 border-t border-slate-100 pt-4 text-xs leading-relaxed text-slate-400">
          This is a read-only handoff package. It shows only the messages the
          sender chose to include; the underlying mailbox is not accessible from
          here.
        </footer>
      </div>
    </article>
  );
}

function ClaimRow({ claim }: { claim: RecipientClaim }) {
  const cited = claim.source_message_id_headers.length;
  return (
    <li className="rounded-md border border-slate-100 bg-white px-3 py-2 text-sm shadow-sm">
      <div className="text-slate-800">{claim.text}</div>
      {cited > 0 ? (
        <div className="mt-1 text-xs text-slate-400">
          {cited} supporting message{cited === 1 ? "" : "s"}
        </div>
      ) : null}
    </li>
  );
}
