import { useEffect, useRef, useState } from "react";
import {
  askRecipientPackage,
  getRecipientPackage,
  startRecipientSession,
} from "../api/client";
import type {
  RecipientAskResponse,
  RecipientClaim,
  RecipientEvidence,
  RecipientPackage as RecipientPackageData,
  RecipientSession,
} from "../api/types";

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
 *   3. POSTs it to /api/handoff/recipient/session for a short-lived session token,
 *      which it persists in sessionStorage (token + expiry + package_id only —
 *      NEVER the capability code, NEVER localStorage, and neither token is logged),
 *   4. reads the package via GET /api/handoff/recipient/package with that token,
 *   5. renders package-local claims + evidence + a constant privacy posture.
 *
 * Hard privacy invariants honored here (spec §8/§10): no mailbox id, no exclusion
 * counts, no Gmail/source/open_url affordance, no live-search suggestion, and no
 * signal about whether sensitive content existed. Every failure (invalid /
 * expired / revoked / already-consumed code, dead session, transport error)
 * collapses to ONE neutral "unavailable" state — no oracle about the cause.
 *
 * Refresh behavior: because the capability code is one-time and already stripped
 * from the URL, a refresh has no code to re-exchange — and reopening the original
 * link would replay a consumed code and fail. So the SHORT-LIVED session token is
 * persisted in sessionStorage (tab-scoped, cleared when the tab closes): on a
 * refresh with no fragment we resume from that stored token and re-render the
 * package. If the stored session is expired/revoked (GET 403) or its expiry has
 * passed, we clear sessionStorage and fall back to the neutral state. A brand-new
 * tab/browser (empty sessionStorage) reopening a consumed link correctly shows
 * the neutral unavailable state.
 *
 * No frontend test runner exists in this repo (docs/s15-verification-matrix.md);
 * verified via `npm run build` + the manual demo in the S17.6 request.
 */

type Phase = "opening" | "loading" | "ready" | "unavailable" | "nolink";

// Narrowly named, tab-scoped key. Holds ONLY the short-lived session token and
// its metadata — never the capability code, never mailbox/content data.
const SESSION_KEY = "ekc_recipient_handoff_session";

interface StoredSession {
  session_token: string;
  expires_at: string;
  package_id: string;
}

function storeSession(s: RecipientSession): void {
  try {
    const payload: StoredSession = {
      session_token: s.session_token,
      expires_at: s.expires_at,
      package_id: s.package_id,
    };
    window.sessionStorage.setItem(SESSION_KEY, JSON.stringify(payload));
  } catch {
    // Storage unavailable (private mode / quota): the session simply won't
    // survive a refresh. Non-fatal — the current render still works.
  }
}

function readStoredSession(): StoredSession | null {
  try {
    const raw = window.sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as Partial<StoredSession>;
    if (typeof p?.session_token === "string" && typeof p?.expires_at === "string") {
      return { session_token: p.session_token, expires_at: p.expires_at, package_id: p.package_id ?? "" };
    }
    return null;
  } catch {
    return null;
  }
}

function clearStoredSession(): void {
  try {
    window.sessionStorage.removeItem(SESSION_KEY);
  } catch {
    // ignore
  }
}

function isFuture(iso: string): boolean {
  const t = new Date(iso).getTime();
  return !Number.isNaN(t) && t > Date.now();
}

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
  // The live session token, held in memory for package-local "ask" calls. This
  // is the same short-lived token already persisted in sessionStorage; keeping a
  // ref avoids re-reading storage and never adds a new persistence site.
  const sessionTokenRef = useRef<string | null>(null);
  // Guard so the one-time code is exchanged exactly once, even under React
  // StrictMode's dev double-invoke of effects (a second exchange of a spent code
  // would 403 and wrongly clobber a good render).
  const startedRef = useRef(false);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    // Read the one-time code from the fragment, then strip it. replaceState (not
    // pushState) means Back does not resurface the code, and the address bar no
    // longer shows it. The code is never written to state, storage, or logged.
    const hash = window.location.hash.startsWith("#") ? window.location.hash.slice(1) : "";
    const code = new URLSearchParams(hash).get("c");
    if (window.location.hash) {
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
    }

    // Fetch the package for a token and render it; on any failure clear the
    // stored session and collapse to the neutral state (no oracle about why).
    const renderWithToken = async (token: string): Promise<void> => {
      try {
        setPhase("loading");
        const data = await getRecipientPackage(token);
        sessionTokenRef.current = token; // enable package-local ask for this session
        setPkg(data);
        setPhase("ready");
      } catch {
        clearStoredSession();
        setPhase("unavailable");
      }
    };

    // The one-time exchange must run to completion once; we deliberately do NOT
    // cancel it on unmount (StrictMode simulates an unmount/remount on the same
    // instance, and cancelling would drop the only valid exchange).
    void (async () => {
      if (code) {
        // First open from a share link: exchange the one-time code, persist the
        // short-lived session so a later refresh can resume, then load.
        try {
          const session = await startRecipientSession(code);
          storeSession(session);
          await renderWithToken(session.session_token);
        } catch {
          // Invalid/expired/revoked/consumed/transport all look identical.
          clearStoredSession();
          setPhase("unavailable");
        }
        return;
      }

      // No fragment (e.g. a refresh): resume from the persisted short-lived
      // session if one is still within its lifetime.
      const stored = readStoredSession();
      if (!stored) {
        setPhase("nolink");
        return;
      }
      if (!isFuture(stored.expires_at)) {
        clearStoredSession();
        setPhase("unavailable");
        return;
      }
      await renderWithToken(stored.session_token);
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
      <PackageDocument pkg={pkg} sessionToken={sessionTokenRef.current} />
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

function PackageDocument({
  pkg,
  sessionToken,
}: {
  pkg: RecipientPackageData;
  sessionToken: string | null;
}) {
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

        {/* Package-local ask — answers only from this package's evidence. */}
        {sessionToken ? <AskBox sessionToken={sessionToken} /> : null}

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
                <EvidenceItem key={e.message_id_header} ev={e} />
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

/** One snapshotted evidence card. No message-id link, no Gmail/source affordance —
 * the recipient reads the snapshot only. Reused by the evidence list and the ask
 * answer's citations. */
function EvidenceItem({ ev }: { ev: RecipientEvidence }) {
  return (
    <li className="rounded-md border border-slate-200 bg-slate-50 p-4">
      <div className="text-sm font-medium text-slate-800">{ev.subject || "(no subject)"}</div>
      <div className="mt-0.5 text-xs text-slate-500">
        {ev.sender_display || "Unknown sender"}
        {ev.sender_domain ? <> · {ev.sender_domain}</> : null}
        {ev.date ? <> · {fmtDate(ev.date)}</> : null}
      </div>
      {ev.body_snapshot ? (
        <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-slate-700">
          {ev.body_snapshot}
        </p>
      ) : null}
    </li>
  );
}

type AskState =
  | { kind: "idle" }
  | { kind: "asking" }
  | { kind: "answered"; resp: RecipientAskResponse }
  | { kind: "error" };

/**
 * Package-local ask (S17.9). Sends the question to the recipient ask endpoint and
 * renders the deterministic answer entirely from this package's own claims +
 * evidence. Citations are package evidence cards only — no message-id link, no
 * Gmail/source affordance, no mailbox id. A no-evidence answer and an error both
 * render neutral copy that never hints whether excluded content exists.
 */
function AskBox({ sessionToken }: { sessionToken: string }) {
  const [query, setQuery] = useState("");
  const [state, setState] = useState<AskState>({ kind: "idle" });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q || state.kind === "asking") return;
    setState({ kind: "asking" });
    try {
      const resp = await askRecipientPackage(sessionToken, q);
      setState({ kind: "answered", resp });
    } catch {
      setState({ kind: "error" });
    }
  };

  return (
    <section className="mt-8 rounded-md border border-indigo-200 bg-indigo-50 p-4">
      <h2 className="text-sm font-semibold text-indigo-900">Ask about this handoff</h2>
      <p className="mt-0.5 text-xs text-indigo-700">
        Answers come only from this package — the sender's mailbox is not searched.
      </p>
      <form className="mt-3 flex gap-2" onSubmit={submit}>
        <input
          type="text"
          className="min-w-0 flex-1 rounded border border-indigo-300 bg-white px-3 py-1.5 text-sm text-slate-800"
          placeholder="e.g. What's the status of the Atlas cutover?"
          value={query}
          onChange={(ev) => setQuery(ev.target.value)}
        />
        <button
          type="submit"
          className="shrink-0 rounded-md bg-indigo-700 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-600 disabled:bg-indigo-300"
          disabled={state.kind === "asking" || !query.trim()}
        >
          {state.kind === "asking" ? "Asking…" : "Ask"}
        </button>
      </form>

      {state.kind === "error" ? (
        <p className="mt-3 text-sm text-slate-600">
          This handoff package isn't available to answer right now.
        </p>
      ) : null}

      {state.kind === "answered" ? (
        <div className="mt-3">
          <p className="text-sm text-slate-700">{state.resp.message}</p>
          {state.resp.answered ? (
            <>
              {state.resp.claims.length > 0 ? (
                <ul className="mt-2 space-y-2">
                  {state.resp.claims.map((c) => (
                    <li
                      key={c.id}
                      className="rounded-md border border-indigo-100 bg-white px-3 py-2 text-sm text-slate-800"
                    >
                      {c.text}
                    </li>
                  ))}
                </ul>
              ) : null}
              {state.resp.evidence.length > 0 ? (
                <div className="mt-3">
                  <div className="text-xs font-semibold uppercase tracking-wide text-indigo-500">
                    From these messages
                  </div>
                  <ul className="mt-2 space-y-3">
                    {state.resp.evidence.map((e) => (
                      <EvidenceItem key={e.message_id_header} ev={e} />
                    ))}
                  </ul>
                </div>
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
