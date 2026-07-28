import { useEffect, useMemo, useRef, useState } from "react";
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
import {
  buildCoverageAreas,
  peopleDetailForEvidence,
  type CoverageArea,
} from "../utils/coverageAreas";

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
        <div className="animate-pulse text-sm text-muted">
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
    <div className="min-h-full bg-app2">
      <div className="mx-auto w-full max-w-[92rem] px-4 py-8 sm:px-6 lg:px-8">
        {children}
      </div>
    </div>
  );
}

function NeutralCard({ heading, body }: { heading: string; body: string }) {
  return (
    <div className="mt-10 rounded-lg border border-line bg-surface p-8 text-center shadow-sm">
      <h1 className="text-lg font-semibold text-ink">{heading}</h1>
      <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted">{body}</p>
    </div>
  );
}

// Heading for any claim kind not covered by BRIEF_SECTIONS (rendered under
// "key facts" fallbacks so no kind is ever dropped).
const PANEL_KIND_HEADING: Record<string, string> = {
  decision: "Decisions",
  open_loop: "Open loops / next actions",
  briefing: "Briefing",
  project_state: "Project state",
  blocker: "Blockers",
  person_note: "People notes",
};

function PackageDocument({
  pkg,
  sessionToken,
}: {
  pkg: RecipientPackageData;
  sessionToken: string | null;
}) {
  // Coverage areas are derived purely from this package's claims + evidence.
  const areas = useMemo(
    () => buildCoverageAreas(pkg.claims, pkg.evidence),
    [pkg.claims, pkg.evidence],
  );
  const [selectedId, setSelectedId] = useState<string | null>(areas[0]?.id ?? null);
  const selected = areas.find((a) => a.id === selectedId) ?? areas[0] ?? null;

  // Workspace-level tab so package-local Ask is always one click from the top —
  // never buried at the bottom of the brief/evidence stack.
  const [tab, setTab] = useState<"brief" | "ask">("brief");

  // Resolve a claim's cited headers to their in-package evidence, so evidence can
  // render directly under the claim it supports. Package-local only.
  const byHeader = useMemo(
    () => new Map(pkg.evidence.map((e) => [e.message_id_header, e])),
    [pkg.evidence],
  );
  const resolveEvidence = (headers: string[]): RecipientEvidence[] =>
    headers.map((h) => byHeader.get(h)).filter((e): e is RecipientEvidence => e != null);

  return (
    <article className="overflow-hidden rounded-lg bg-surface shadow-sm ring-1 ring-line">
      {/* Document header band — the delivered-package identity. */}
      <header className="bg-band px-6 py-6 text-onband sm:px-8 sm:py-7">
        <div className="text-xs font-semibold uppercase tracking-widest text-onband">
          Handoff package · Read-only
        </div>
        <h1 className="mt-2 text-2xl font-semibold leading-tight">
          {pkg.title || "Coverage handoff"}
        </h1>
        <div className="mt-3 text-sm text-onband">
          Prepared for you by <span className="font-medium text-onband">{pkg.creator_email}</span>
          {pkg.reason ? <> · {pkg.reason}</> : null}
        </div>
        <div className="mt-1 text-xs text-onband">
          Shared {fmtDate(pkg.published_at)}
          {pkg.expires_at ? <> · access expires {fmtDate(pkg.expires_at)}</> : null}
        </div>
      </header>

      <div className="px-6 py-6 sm:px-8">
        {/* Privacy posture — reassurance, constant, no counts/oracle. */}
        <aside className="rounded-md border border-jade bg-jade-soft px-4 py-3 text-sm text-jade">
          <div className="font-medium">Scope-limited · Sensitive content excluded</div>
          <p className="mt-1 leading-relaxed text-jade">{pkg.privacy_posture.note}</p>
        </aside>

        {/* Workspace tabs — Ask sits at the top, alongside the coverage brief. */}
        <div className="mt-6 flex gap-1 border-b border-line" role="tablist">
          <WorkspaceTab active={tab === "brief"} onClick={() => setTab("brief")}>
            Coverage brief
          </WorkspaceTab>
          {sessionToken ? (
            <WorkspaceTab active={tab === "ask"} onClick={() => setTab("ask")}>
              Ask about this handoff
            </WorkspaceTab>
          ) : null}
        </div>

        <div className="mt-5">
          {tab === "ask" && sessionToken ? (
            <AskBox sessionToken={sessionToken} />
          ) : areas.length === 0 ? (
            <p className="text-sm text-faint">This handoff has no summary points.</p>
          ) : (
            // Coverage brief: area rail · brief (claim-attached evidence) · people.
            <div className="lg:grid lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-6">
              <CoverageAreaRail
                areas={areas}
                selectedId={selected?.id ?? null}
                onSelect={setSelectedId}
              />
              <div className="mt-6 min-w-0 lg:mt-0">
                {selected ? (
                  <AreaBrief area={selected} resolveEvidence={resolveEvidence} />
                ) : null}
                {selected ? <PeopleSection area={selected} /> : null}
              </div>
            </div>
          )}
        </div>

        <footer className="mt-9 border-t border-line pt-4 text-xs leading-relaxed text-faint">
          This is a read-only handoff package. It shows only the messages the
          sender chose to include; the underlying mailbox is not accessible from
          here.
        </footer>
      </div>
    </article>
  );
}

/** A workspace-level tab button (Coverage brief / Ask). */
function WorkspaceTab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={
        "-mb-px border-b-2 px-3 py-2 text-sm font-medium " +
        (active
          ? "border-brass text-brass"
          : "border-transparent text-muted hover:text-ink")
      }
    >
      {children}
    </button>
  );
}

/** Left navigation rail — a horizontal scroller on mobile, a vertical rail on
 * desktop. One item per coverage area with compact per-area counts. */
function CoverageAreaRail({
  areas,
  selectedId,
  onSelect,
}: {
  areas: CoverageArea[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <nav aria-label="Coverage areas" className="lg:sticky lg:top-4 lg:self-start">
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted">
          Coverage areas
        </span>
        <span className="text-xs text-faint">{areas.length}</span>
      </div>
      <div className="flex gap-2 overflow-x-auto pb-1 lg:flex-col lg:gap-1 lg:overflow-visible lg:pb-0">
        {areas.map((a) => {
          const active = a.id === selectedId;
          return (
            <button
              key={a.id}
              type="button"
              onClick={() => onSelect(a.id)}
              aria-current={active ? "true" : undefined}
              className={
                "w-56 shrink-0 rounded-md border px-3 py-2 text-left lg:w-full " +
                (active
                  ? "border-brass bg-brass-soft ring-1 ring-brass"
                  : "border-line bg-surface hover:bg-app2")
              }
              title={a.label}
            >
              <div className={"truncate text-sm font-medium " + (active ? "text-ink" : "text-ink")}>
                {a.label}
              </div>
              <div className="mt-0.5 truncate text-xs text-faint">
                {a.decisionCount} dec · {a.openLoopCount} open · {a.evidenceCount} msg
              </div>
            </button>
          );
        })}
      </div>
    </nav>
  );
}

// Middle-panel sections, in reading priority. Decisions/outcomes → next actions →
// blockers → everything else as "key facts".
const BRIEF_SECTIONS: { heading: string; kinds: string[] }[] = [
  { heading: "Decisions & outcomes", kinds: ["decision"] },
  { heading: "Open loops / next actions", kinds: ["open_loop"] },
  { heading: "Blockers & follow-ups", kinds: ["blocker"] },
  { heading: "Key facts", kinds: ["briefing", "project_state", "person_note"] },
];

/** Middle reading surface: a compact brief for the selected coverage area.
 * Evidence is attached INLINE to the specific claim it supports (an expandable
 * disclosure inside each claim), not a separate global section. A claim with no
 * in-package evidence is dropped entirely ("no citation, no claim"). */
function AreaBrief({
  area,
  resolveEvidence,
}: {
  area: CoverageArea;
  resolveEvidence: (headers: string[]) => RecipientEvidence[];
}) {
  // Only claims backed by in-package evidence are shown.
  const supported = area.claims.filter(
    (c) => resolveEvidence(c.source_message_id_headers).length > 0,
  );
  const grouped = supported.reduce<Record<string, RecipientClaim[]>>((acc, c) => {
    (acc[c.kind] ??= []).push(c);
    return acc;
  }, {});
  const shownKinds = new Set(BRIEF_SECTIONS.flatMap((s) => s.kinds));
  const extraKinds = Object.keys(grouped).filter((k) => !shownKinds.has(k));

  const renderClaims = (claims: RecipientClaim[]) => (
    <ul className="mt-2 space-y-2">
      {claims.map((c) => (
        <ClaimRow key={c.id} claim={c} evidence={resolveEvidence(c.source_message_id_headers)} />
      ))}
    </ul>
  );

  return (
    <section className="rounded-lg border border-line bg-surface p-5">
      <h2 className="text-lg font-semibold text-ink">{area.label}</h2>
      <div className="mt-1 text-xs text-muted">
        {area.decisionCount} decision{area.decisionCount === 1 ? "" : "s"} ·{" "}
        {area.openLoopCount} open loop{area.openLoopCount === 1 ? "" : "s"} ·{" "}
        {area.evidenceCount} message{area.evidenceCount === 1 ? "" : "s"}
      </div>

      {BRIEF_SECTIONS.map((sec) => {
        const claims = sec.kinds.flatMap((k) => grouped[k] ?? []);
        if (claims.length === 0) return null;
        return (
          <div key={sec.heading} className="mt-4">
            <div className="text-xs font-semibold uppercase tracking-wide text-brass">
              {sec.heading}
            </div>
            {renderClaims(claims)}
          </div>
        );
      })}

      {extraKinds.map((kind) => (
        <div key={kind} className="mt-4">
          <div className="text-xs font-semibold uppercase tracking-wide text-brass">
            {PANEL_KIND_HEADING[kind] ?? kind}
          </div>
          {renderClaims(grouped[kind])}
        </div>
      ))}

      {supported.length === 0 ? (
        <p className="mt-3 text-sm text-faint">No summary points in this area.</p>
      ) : null}
    </section>
  );
}

/** Related people/domains for the selected area, derived only from package-local
 * evidence sender fields with honest, non-invented context labels. */
function PeopleSection({ area }: { area: CoverageArea }) {
  const people = peopleDetailForEvidence(area.evidence);
  if (people.length === 0) return null;
  return (
    <section className="mt-6 rounded-lg border border-line bg-surface p-5">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
        Related people &amp; domains
      </h2>
      <p className="mt-0.5 text-xs text-faint">
        Derived from this area's cited evidence.
      </p>
      <ul className="mt-3 space-y-2">
        {people.map((p) => (
          <li
            key={`${p.name}|${p.domain}`}
            className="flex items-baseline justify-between gap-3 rounded-md border border-line bg-app2 px-3 py-2"
          >
            <div className="min-w-0">
              <div className="truncate text-sm text-ink">{p.name}</div>
              {p.domain ? <div className="truncate text-xs text-muted">{p.domain}</div> : null}
            </div>
            <span className="shrink-0 rounded-full border border-line bg-surface px-2 py-0.5 text-xs text-muted">
              {p.context}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** One claim, with its OWN supporting evidence inline (an expandable disclosure
 * showing only the messages this claim cites). */
function ClaimRow({ claim, evidence }: { claim: RecipientClaim; evidence: RecipientEvidence[] }) {
  return (
    <li className="rounded-md border border-line bg-surface px-3 py-2 text-sm shadow-sm">
      <div className="text-ink">{claim.text}</div>
      {evidence.length > 0 ? (
        <details className="mt-1.5">
          <summary className="cursor-pointer select-none text-xs font-medium text-brass hover:text-brass">
            {evidence.length} supporting message{evidence.length === 1 ? "" : "s"}
          </summary>
          <ul className="mt-2 space-y-2">
            {evidence.map((e) => (
              <EvidenceItem key={e.message_id_header} ev={e} />
            ))}
          </ul>
        </details>
      ) : null}
    </li>
  );
}

/** One snapshotted evidence card. No message-id link, no Gmail/source affordance —
 * the recipient reads the snapshot only. Reused by the coverage-area evidence
 * list and the ask answer's citations. */
function EvidenceItem({ ev }: { ev: RecipientEvidence }) {
  return (
    <li className="rounded-md border border-line bg-surface p-4">
      <div className="text-sm font-medium text-ink">{ev.subject || "(no subject)"}</div>
      <div className="mt-0.5 text-xs text-muted">
        {ev.sender_display || "Unknown sender"}
        {ev.sender_domain ? <> · {ev.sender_domain}</> : null}
        {ev.date ? <> · {fmtDate(ev.date)}</> : null}
      </div>
      {ev.body_snapshot ? (
        <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-ink">
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
    <section className="rounded-md border border-brass bg-brass-soft p-4">
      <h2 className="text-sm font-semibold text-ink">Ask about this handoff</h2>
      <p className="mt-0.5 text-xs text-brass">
        Answers come only from this package — the sender's mailbox is not searched.
      </p>
      <form className="mt-3 flex gap-2" onSubmit={submit}>
        <input
          type="text"
          className="min-w-0 flex-1 rounded border border-brass bg-surface px-3 py-1.5 text-sm text-ink"
          placeholder="e.g. What's the status of the Atlas cutover?"
          value={query}
          onChange={(ev) => setQuery(ev.target.value)}
        />
        <button
          type="submit"
          className="shrink-0 rounded-md bg-brass px-4 py-1.5 text-sm font-medium text-onbrass hover:bg-brass disabled:bg-brass-soft disabled:text-faint"
          disabled={state.kind === "asking" || !query.trim()}
        >
          {state.kind === "asking" ? "Asking…" : "Ask"}
        </button>
      </form>

      {state.kind === "error" ? (
        <p className="mt-3 text-sm text-muted">
          This handoff package isn't available to answer right now.
        </p>
      ) : null}

      {state.kind === "answered" ? (
        <div className="mt-3">
          <p className="text-sm text-ink">{state.resp.message}</p>
          {state.resp.answered ? (
            <>
              {state.resp.claims.length > 0 ? (
                <ul className="mt-2 space-y-2">
                  {state.resp.claims.map((c) => (
                    <li
                      key={c.id}
                      className="rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink"
                    >
                      {c.text}
                    </li>
                  ))}
                </ul>
              ) : null}
              {state.resp.evidence.length > 0 ? (
                <div className="mt-3">
                  <div className="text-xs font-semibold uppercase tracking-wide text-brass">
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
