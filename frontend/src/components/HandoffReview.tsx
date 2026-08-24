import { useEffect, useMemo, useState } from "react";
import {
  createHandoff,
  describeError,
  generateHandoff,
  getHandoff,
  getReturnContext,
  handoffExportUrl,
  newVersionHandoff,
  publishHandoff,
  revokeHandoff,
  updateHandoffScope,
} from "../api/client";
import type {
  HandoffEvidence,
  HandoffPackage,
  HandoffScopeData,
  PublishResponse,
  ReturnContext,
  SafetyFinding,
} from "../api/types";
import { navigate, usePathname } from "../router";
import { useProjects } from "../hooks/useProjects";
import {
  buildHandoffProjectGroups,
  filterHandoffGroup,
  type HandoffGroup,
} from "../utils/handoffGroups";
import { ReturnBanner, ReturnCreatePanel } from "./ReturnHandoff";

const HANDOFF_BASE = "/app/handoff";

/**
 * Creator scope-review + publish surface (S17.4 review, S17.7 publish).
 *
 * The covered employee inspects and prunes a candidate handoff package built from
 * their OWN mailbox, then publishes it to a single recipient. This is deliberately
 * closer to the old mailbox-review UX (own data), and is NOT the recipient package
 * view (that read-only view lives at /handoff/recipient, S17.6). Publishing here
 * (PublishControls) freezes the package and mints the one-time recipient link; the
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

/**
 * Explicit copy for an empty generated candidate (S17.13). Uses the creator-only
 * `generation` diagnostic so the message is honest about the cause — critically,
 * that widening the date range will NOT help when the mailbox has no extracted
 * events at all. Falls back to the old generic copy if no diagnostic is present.
 */
function generationEmptyMessage(pkg: HandoffPackage): string {
  switch (pkg.generation?.code) {
    case "no_events_for_mailbox":
      return (
        "No handoff claims were generated because this mailbox has no extracted " +
        "handoff events at all. Widening the date range will not help. Event " +
        "extraction (L1 enrichment) must run for this mailbox before a handoff " +
        "package can be generated."
      );
    case "no_events_in_scope":
      return (
        "No handoff claims were generated because no extracted events fall in the " +
        "selected scope. Try widening the date range or including more " +
        "projects/people."
      );
    case "all_events_excluded_by_policy":
      return (
        "No handoff claims were generated: the events in this scope were all " +
        "withheld by the sensitivity / noise / exclusion policy, so nothing here " +
        "is safe to share. Try a different scope."
      );
    default:
      return (
        "No package claims were generated from the current scope. Try a wider " +
        "scope, or run against a mailbox with extracted events."
      );
  }
}

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
  // S34: routed from the recipient view's "Create return handoff" via
  // /app/handoff?return_from=<original_package_id>. Recomputed on navigation
  // (usePathname re-renders); used only to seed the return-create panel.
  const returnFrom = pathname === HANDOFF_BASE
    ? new URLSearchParams(window.location.search).get("return_from")
    : null;

  const [pkg, setPkg] = useState<HandoffPackage | null>(null);
  const [returnCtx, setReturnCtx] = useState<ReturnContext | null>(null);
  const [reason, setReason] = useState("vacation");
  const [title, setTitle] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState<
    null | "create" | "scope" | "generate" | "remove" | "restore" | "publish" | "revoke" | "version"
  >(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // Transient publish result: holds the ONE-TIME capability code / share fragment
  // in React state ONLY, for the lifetime of this view. Never stored, never
  // logged. Cleared when the open package changes (see the reset effect below),
  // so a refresh cannot recover it.
  const [share, setShare] = useState<PublishResponse | null>(null);
  // S37 review ergonomics: a text filter to jump to one coverage area, and per-
  // area collapse (of the whole area) / evidence-disclosure state so a large
  // package reads as grouped claims first, with evidence revealed on demand.
  const [reviewFilter, setReviewFilter] = useState("");
  const [collapsedAreas, setCollapsedAreas] = useState<Set<string>>(() => new Set());
  const [openEvidence, setOpenEvidence] = useState<Set<string>>(() => new Set());

  // Load / validate the package named in the URL for the current mailbox.
  // Runs on route or mailbox change; self-heals a route that points at a
  // missing package or another mailbox's package by returning to the base route.
  useEffect(() => {
    let cancelled = false;
    if (!routeId) {
      // Back to the create surface (e.g. "Start over"). Reset ALL route-load
      // state — critically `loading`, which if left true from a prior load keeps
      // the "Loading handoff package…" branch (`loading && !pkg`) showing forever.
      setPkg(null);
      setLoading(false);
      setError(null);
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

  // S34: when the loaded package is a return handoff, fetch its safe return
  // context (carried coverage areas, seed method, suggested recipient) for framing.
  useEffect(() => {
    if (pkg && pkg.package_type === "return_delta") {
      getReturnContext(pkg.id)
        .then(setReturnCtx)
        .catch(() => setReturnCtx(null));
    } else {
      setReturnCtx(null);
    }
  }, [pkg?.id, pkg?.package_type]);

  // S37: group by REAL project identity. This is the creator's OWN mailbox review
  // surface, so we may use live project metadata — `claim.project_id` resolved to
  // a label from the mailbox project list. (The recipient text-clustering
  // primitive over-merged dense rich data into one giant area and is the wrong
  // tool here.) No recipient live-mailbox access, no snapshot change.
  const { projects } = useProjects(mailboxId);
  const labelById = useMemo(
    () => new Map(projects.map((p) => [p.id, p.label])),
    [projects],
  );
  // Groups already include an "Other evidence" bucket for anything a claim no
  // longer cites, so the visible evidence total always equals the safe count.
  const groups = useMemo<HandoffGroup[]>(
    () =>
      pkg && pkg.claims.length > 0
        ? buildHandoffProjectGroups(pkg.claims, pkg.evidence, labelById)
        : [],
    [pkg?.id, pkg?.claims, pkg?.evidence, labelById],
  );

  // A new package clears the filter; a changed group set (regenerate, prune)
  // resets disclosure so stale ids never point at the wrong group.
  useEffect(() => {
    setReviewFilter("");
  }, [pkg?.id]);
  useEffect(() => {
    setCollapsedAreas(new Set());
    setOpenEvidence(new Set());
  }, [pkg?.id, groups.length]);

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

  // Clear ONLY the creator's manual evidence removals and rebuild from the full
  // safe scope. Policy exclusions (sensitivity/noise) are re-applied at generate,
  // so this never restores sensitive/noise content — only what you removed by hand.
  const restoreAllEvidence = () =>
    pkg &&
    run("restore", async () => {
      await updateHandoffScope(pkg.id, { ...scopeBody(pkg), excluded_message_id_headers: [] });
      return generateHandoff(pkg.id);
    });

  // Publish freezes the package and returns the one-time code exactly once; we
  // hold the result in transient state (setShare) and never persist/log it.
  const publish = async (
    recipientEmail: string,
    expiresInDays: number | null,
    safetyAck?: { reason: string; acknowledged_finding_ids: string[] },
  ) => {
    if (!pkg) return;
    setBusy("publish");
    setError(null);
    try {
      const resp = await publishHandoff(pkg.id, {
        recipient_email: recipientEmail,
        ...(expiresInDays ? { expires_in_days: expiresInDays } : {}),
        ...(safetyAck ? { safety_ack: safetyAck } : {}),
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

  // Fork a frozen package into a fresh draft in the same lineage and open it —
  // the honest recovery path for a lost link, wrong recipient, or scope revision.
  const newVersion = async () => {
    if (!pkg) return;
    setBusy("version");
    setError(null);
    try {
      const draft = await newVersionHandoff(pkg.id);
      navigate(`${HANDOFF_BASE}/${draft.id}`); // load the new draft (clears share)
    } catch (e) {
      setError(describeError(e).message);
    } finally {
      setBusy(null);
    }
  };

  const copyId = (value: string) => {
    navigator.clipboard?.writeText(value).catch(() => window.prompt("Copy this Message-ID:", value));
  };

  const excludedHeaders = new Set(pkg?.scope.excluded_message_id_headers ?? []);
  const excludedCount = excludedHeaders.size; // creator manual removals in this scope
  // Only draft/generated packages are editable. Once published (or revoked) the
  // package is immutable: scope edits, regenerate, and evidence removal are all
  // disabled in the UI (§immutability). Backend also rejects these transitions.
  const mutable = !pkg || pkg.status === "draft" || pkg.status === "generated";

  // ── S37 review derivations (filter within groups / jump / counts) ─────────
  const q = reviewFilter.trim().toLowerCase();
  // Filter WITHIN each group; keep only groups with a surviving claim or evidence.
  const visibleGroups = groups
    .map((g) => ({ group: g, ...filterHandoffGroup(g, q) }))
    .filter((fg) => fg.claims.length > 0 || fg.evidence.length > 0);
  const shownClaims = visibleGroups.reduce((n, fg) => n + fg.claims.length, 0);
  const shownEvidenceHeaders = new Set<string>();
  for (const fg of visibleGroups)
    for (const e of fg.evidence) shownEvidenceHeaders.add(e.message_id_header);
  const shownEvidence = shownEvidenceHeaders.size;
  const totalClaims = pkg?.claims.length ?? 0;
  const totalEvidence = pkg?.evidence.length ?? 0;
  const allGroupIds = groups.map((g) => g.id);
  const toggleAreaCollapsed = (id: string) =>
    setCollapsedAreas((p) => {
      const n = new Set(p);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const toggleAreaEvidence = (id: string) =>
    setOpenEvidence((p) => {
      const n = new Set(p);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const expandAllAreas = () => setCollapsedAreas(new Set());
  const collapseAllAreas = () => setCollapsedAreas(new Set(allGroupIds));
  const jumpToGroup = (id: string) => {
    // Expanding first so the target is on-screen before we scroll to it.
    setCollapsedAreas((p) => {
      const n = new Set(p);
      n.delete(id);
      return n;
    });
    requestAnimationFrame(() =>
      document
        .getElementById(`handoff-${id}`)
        ?.scrollIntoView({ behavior: "smooth", block: "start" }),
    );
  };

  return (
    <div className="mx-auto w-full max-w-[92rem] px-4 py-6 sm:px-6">
      <h2 className="text-lg font-semibold text-ink">Review handoff package</h2>
      <p className="mt-1 max-w-2xl text-sm text-muted">
        Inspect and prune what a coverage handoff would reveal, from your own
        mailbox, <strong>before publishing</strong>. This is your review surface,
        not the recipient view. Sensitive and noise messages are excluded
        automatically; you can remove anything else. When it looks right,{" "}
        <strong>publish</strong> to freeze it and mint a one-time recipient link.
      </p>

      {/* Why can Overview read 0/0/limited while Handoff works? (S17.20) */}
      <details className="mt-3 max-w-2xl rounded-md border border-line bg-app2 px-3 py-2 text-xs text-muted">
        <summary className="cursor-pointer font-medium text-ink">
          Why does Overview show 0 people / projects for this mailbox?
        </summary>
        <p className="mt-2 leading-relaxed">
          The seeded Handoff mailbox is provisioned for{" "}
          <strong>handoff package generation</strong> (messages + extracted events),
          not the full graph profile. The Overview readiness strip reports{" "}
          <strong>whole-workspace</strong> readiness (People, Projects, Retrieval),
          not handoff-generation readiness, so 0/0/limited here is expected and does
          not block publishing. The Cover-for-me, Relationship Map, and Network Map
          demos use the <strong>puluo</strong> mailbox instead.
        </p>
      </details>

      {error ? (
        <div className="status-failed mt-4" role="alert">
          {error}
        </div>
      ) : null}

      {/* ── Create (or loading a deep-linked package) ─────────────────────── */}
      {loading && !pkg ? (
        <p className="mt-6 text-sm text-muted">Loading handoff package…</p>
      ) : !pkg ? (
        <section className="rounded-md border border-line bg-surface p-4">
          <h3 className="text-sm font-semibold text-ink">Start a handoff</h3>
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <label className="flex flex-col text-xs text-muted">
              Reason
              <select
                className="mt-1 rounded border border-line2 px-2 py-1 text-sm"
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
            <label className="flex flex-1 flex-col text-xs text-muted">
              Title (optional)
              <input
                type="text"
                className="mt-1 rounded border border-line2 px-2 py-1 text-sm"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder='e.g. "Covering Atlas while I&apos;m out"'
              />
            </label>
            <button
              type="button"
              className="rounded-md bg-brass px-4 py-2 text-sm font-medium text-onbrass hover:bg-brass disabled:bg-brass-soft disabled:text-faint"
              onClick={create}
              disabled={busy !== null}
            >
              {busy === "create" ? "Creating…" : "Create draft"}
            </button>
          </div>
          <ReturnCreatePanel mailboxId={mailboxId} initialOriginalId={returnFrom ?? undefined} />
        </section>
      ) : (
        <>
          <PackageHeader
            pkg={pkg}
            mutable={mutable}
            onStartOver={() => navigate(HANDOFF_BASE)}
          />

          {pkg.package_type === "return_delta" ? (
            <ReturnBanner ctx={returnCtx} pkg={pkg} />
          ) : null}

          {/* Workspace layout: section nav (wide) · center review · sticky
              action rail. The rail carries every action (scope, generate,
              publish/share/revoke/revise/export) so Publish is reachable near
              the top — on mobile it stacks first, on desktop it is a sticky
              right rail. */}
          <div className="mt-4 grid gap-5 lg:grid-cols-[minmax(0,1fr)_21rem] xl:grid-cols-[9rem_minmax(0,1fr)_21rem]">
            <nav
              className="hidden xl:block xl:sticky xl:top-4 xl:self-start"
              aria-label="Sections"
            >
              <div className="text-[11px] font-semibold uppercase tracking-wider text-faint">
                On this page
              </div>
              <ul className="mt-2 space-y-1">
                {([
                  ["actions", "Scope & actions"],
                  ["review", `Review · ${pkg.claims.length} claims / ${pkg.evidence.length} evidence`],
                  ...(pkg.status !== "draft"
                    ? [["publish", pkg.status === "generated" ? "Publish" : "Share & revoke"] as [string, string]]
                    : []),
                ] as [string, string][]).map(([id, label]) => (
                  <li key={id}>
                    <button
                      type="button"
                      className="w-full rounded px-2 py-1 text-left text-sm text-muted hover:bg-app2 hover:text-ink"
                      onClick={() =>
                        document
                          .getElementById(id)
                          ?.scrollIntoView({ behavior: "smooth", block: "start" })
                      }
                    >
                      {label}
                    </button>
                  </li>
                ))}
              </ul>
            </nav>

            {/* Center: generated claims + evidence (with creator pruning). */}
            <main className="order-2 min-w-0 lg:order-none">
              {pkg.status === "draft" ? (
                <div className="rounded-md border border-line bg-surface p-6 text-sm text-muted">
                  Set a scope (optional) in the actions panel, then click{" "}
                  <strong>Generate package</strong> to build the candidate for
                  review.
                </div>
              ) : (
                <>
                  <ExclusionSummary counts={pkg.exclusion_counts} />
                  <SafetyReviewPanel findings={pkg.findings ?? []} />

                  <section id="review" className="mt-4 scroll-mt-4">
                    {pkg.claims.length === 0 ? (
                      <div className="rounded-md border border-warn-line bg-warn-soft px-3 py-2 text-sm text-warn">
                        {generationEmptyMessage(pkg)}
                      </div>
                    ) : (
                      <>
                        {/* Filter + jump + bulk collapse: navigate the whole
                            package without scrolling the evidence wall. */}
                        <div className="rounded-md border border-line bg-surface p-3">
                          <div className="flex flex-wrap items-center gap-2">
                            <input
                              type="text"
                              value={reviewFilter}
                              onChange={(e) => setReviewFilter(e.target.value)}
                              placeholder="Filter by project, claim, subject, or sender…"
                              className="min-w-[12rem] flex-1 rounded border border-line2 bg-surface px-2 py-1 text-sm text-ink"
                              aria-label="Filter project groups"
                            />
                            <div className="flex items-center gap-2 text-xs">
                              <button
                                type="button"
                                className="text-muted underline underline-offset-2 hover:text-ink"
                                onClick={expandAllAreas}
                              >
                                Expand all
                              </button>
                              <span className="text-faint" aria-hidden>·</span>
                              <button
                                type="button"
                                className="text-muted underline underline-offset-2 hover:text-ink"
                                onClick={collapseAllAreas}
                              >
                                Collapse all
                              </button>
                            </div>
                          </div>
                          <p className="mt-2 text-[11px] text-faint">
                            Grouped into {groups.length} project group
                            {groups.length === 1 ? "" : "s"} by the covered project.
                            Evidence is collapsed under each group; expand to inspect
                            or remove.
                          </p>
                          {q ? (
                            <p className="mt-1 text-[11px] font-medium text-muted">
                              Showing {shownClaims} of {totalClaims} claims ·{" "}
                              {shownEvidence} of {totalEvidence} evidence.
                            </p>
                          ) : null}
                          {visibleGroups.length > 1 ? (
                            <div className="mt-2 flex flex-wrap gap-1.5">
                              {visibleGroups.map((fg) => (
                                <button
                                  key={fg.group.id}
                                  type="button"
                                  onClick={() => jumpToGroup(fg.group.id)}
                                  className="max-w-[16rem] truncate rounded-full border border-line px-2.5 py-1 text-[11px] text-muted hover:bg-app2 hover:text-ink"
                                  title={fg.group.label}
                                >
                                  {fg.group.label} · {fg.claims.length}/{fg.evidence.length}
                                </button>
                              ))}
                            </div>
                          ) : null}
                        </div>

                        {/* Removed-evidence banner + restore-all — kept near the
                            pruning it explains. Restores only YOUR manual removals,
                            never sensitivity/noise exclusions. */}
                        {excludedCount > 0 ? (
                          <div className="mt-3 rounded-md border border-warn-line bg-warn-soft px-3 py-2 text-xs text-warn">
                            <div className="font-medium">
                              {excludedCount} evidence item{excludedCount === 1 ? "" : "s"} removed by
                              you (excluded from this package).
                            </div>
                            <p className="mt-1">
                              Regenerate rebuilds from the current scope, including your removals, so the
                              count stays reduced on purpose. Sensitive/noise content is excluded by
                              policy separately and is never restored here.
                            </p>
                            {mutable ? (
                              <button
                                type="button"
                                className="mt-2 rounded-md border border-warn-line bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-app2 disabled:opacity-50"
                                onClick={restoreAllEvidence}
                                disabled={busy !== null}
                                title="Clear only your manual removals and rebuild from the full safe scope"
                              >
                                {busy === "restore" ? "Restoring…" : "Restore all removed evidence"}
                              </button>
                            ) : (
                              <p className="mt-1">
                                To rebuild from the full safe scope, use{" "}
                                <strong>Create revised version</strong> (below), then restore there.
                              </p>
                            )}
                          </div>
                        ) : null}

                        <p className="mt-3 text-xs text-faint">
                          {mutable
                            ? "Snapshotted safe message content. Remove anything that should not travel with the handoff; a claim left without evidence disappears on regenerate. Removed evidence stays excluded when you regenerate or create a revised version."
                            : "Snapshotted safe message content, frozen at publish. This is exactly what the recipient can read."}
                        </p>

                        <div className="mt-2 space-y-3">
                          {visibleGroups.map((fg) => (
                            <CoverageAreaSection
                              key={fg.group.id}
                              domId={fg.group.id}
                              title={fg.group.label}
                              claims={fg.claims}
                              evidence={fg.evidence}
                              collapsed={collapsedAreas.has(fg.group.id)}
                              evidenceOpen={openEvidence.has(fg.group.id)}
                              onToggleCollapsed={() => toggleAreaCollapsed(fg.group.id)}
                              onToggleEvidence={() => toggleAreaEvidence(fg.group.id)}
                              excludedHeaders={excludedHeaders}
                              onRemove={removeEvidence}
                              onCopy={copyId}
                              busy={busy !== null}
                              mutable={mutable}
                            />
                          ))}

                          {visibleGroups.length === 0 ? (
                            <p className="rounded-md border border-line bg-app2 px-3 py-2 text-sm text-muted">
                              No claims or evidence match “{reviewFilter.trim()}”.
                            </p>
                          ) : null}
                        </div>
                      </>
                    )}
                  </section>
                </>
              )}
            </main>

            {/* Right rail: all actions. order-first = top section on mobile;
                sticky rail on desktop so Publish never hides below evidence. */}
            <aside className="order-first space-y-4 lg:order-none lg:sticky lg:top-4 lg:self-start">
              {mutable ? (
                <div
                  id="actions"
                  className="scroll-mt-4 rounded-md border border-line bg-surface p-4"
                >
                  <h3 className="text-sm font-semibold text-ink">Scope &amp; generate</h3>
                  <div className="mt-3 flex gap-3">
                    <label className="flex flex-1 flex-col text-xs text-muted">
                      From
                      <input
                        type="date"
                        className="mt-1 rounded border border-line2 bg-surface px-2 py-1 text-sm text-ink"
                        value={from}
                        onChange={(e) => setFrom(e.target.value)}
                      />
                    </label>
                    <label className="flex flex-1 flex-col text-xs text-muted">
                      To
                      <input
                        type="date"
                        className="mt-1 rounded border border-line2 bg-surface px-2 py-1 text-sm text-ink"
                        value={to}
                        onChange={(e) => setTo(e.target.value)}
                      />
                    </label>
                  </div>
                  {(pkg.scope.included_project_ids.length > 0 ||
                    pkg.scope.included_person_ids.length > 0 ||
                    pkg.scope.included_thread_ids.length > 0) && (
                    <div className="mt-2 text-[11px] text-faint">
                      Scoped to {pkg.scope.included_project_ids.length} project(s),{" "}
                      {pkg.scope.included_person_ids.length} person(s),{" "}
                      {pkg.scope.included_thread_ids.length} thread(s).
                    </div>
                  )}
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      className="rounded-md border border-line2 px-3 py-2 text-sm text-ink hover:bg-app2 disabled:opacity-50"
                      onClick={saveScope}
                      disabled={busy !== null}
                    >
                      {busy === "scope" ? "Saving…" : "Save scope"}
                    </button>
                    <button
                      type="button"
                      className="flex-1 rounded-md bg-brass px-4 py-2 text-sm font-medium text-onbrass hover:bg-brass disabled:bg-brass-soft disabled:text-faint"
                      onClick={generate}
                      disabled={busy !== null}
                    >
                      {busy === "generate" || busy === "remove"
                        ? "Generating…"
                        : pkg.status === "draft"
                          ? "Generate package"
                          : "Regenerate"}
                    </button>
                  </div>
                </div>
              ) : (
                <div
                  id="actions"
                  className="scroll-mt-4 rounded-md border border-line2 bg-app2 p-4 text-xs text-faint"
                >
                  This package is {pkg.status} and frozen. Scope, regeneration, and
                  evidence removal are locked.
                </div>
              )}

              {pkg.status !== "draft" ? (
                <div id="publish" className="scroll-mt-4">
                  <PublishControls
                    pkg={pkg}
                    share={share}
                    busy={busy}
                    onPublish={publish}
                    onRevoke={revoke}
                    onNewVersion={newVersion}
                    defaultRecipientEmail={
                      pkg.package_type === "return_delta"
                        ? returnCtx?.suggested_recipient_email
                        : undefined
                    }
                  />
                </div>
              ) : null}
            </aside>
          </div>
        </>
      )}
    </div>
  );
}

/**
 * Creator-only pre-publish safety review (S44). Shows deterministic findings the
 * server computed from THIS package's own snapshot, grouped by severity, with a
 * safe category + short explanation and the affected claim/evidence reference. It
 * shows only safe metadata (never the matched sensitive text) and never appears in
 * the recipient or admin surfaces. High-severity findings block publishing (see
 * PublishControls); medium/low only warn.
 */
function SafetyReviewPanel({ findings }: { findings: SafetyFinding[] }) {
  if (findings.length === 0) return null;
  const order: SafetyFinding["severity"][] = ["high", "medium", "low"];
  const style: Record<string, string> = {
    high: "border-danger-line bg-danger-soft text-danger",
    medium: "border-warn-line bg-warn-soft text-warn",
    low: "border-line bg-app2 text-muted",
  };
  const bySev = (sev: string) => findings.filter((f) => f.severity === sev);
  const highCount = bySev("high").length;
  return (
    <section className="mt-4 rounded-md border border-line bg-surface p-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-ink">Safety review</h3>
        {order.map((sev) =>
          bySev(sev).length > 0 ? (
            <span
              key={sev}
              className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${style[sev] ?? style.low}`}
            >
              {bySev(sev).length} {sev}
            </span>
          ) : null,
        )}
      </div>
      <p className="mt-1 text-xs text-faint">
        Detected in this package&apos;s own content before publish. Remove the flagged
        claim/evidence and regenerate to clear a finding.
        {highCount > 0
          ? " High-severity findings block publishing until resolved or acknowledged."
          : ""}
      </p>
      <ul className="mt-2 space-y-1.5">
        {order.flatMap((sev) =>
          bySev(sev).map((f) => (
            <li key={f.id} className="flex flex-wrap items-baseline gap-2 text-xs">
              <span
                className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase ${style[f.severity] ?? style.low}`}
              >
                {f.severity}
              </span>
              <span className="font-medium text-ink">{f.category}</span>
              <span className="text-muted">{f.explanation}</span>
              {f.claim_id ? (
                <span className="text-faint">· claim</span>
              ) : f.evidence_header ? (
                <span className="break-all font-mono text-[10px] text-faint">
                  · {f.evidence_header}
                </span>
              ) : null}
            </li>
          )),
        )}
      </ul>
    </section>
  );
}

function ExclusionSummary({ counts }: { counts: Record<string, number> }) {
  const parts = Object.entries(counts)
    .filter(([, n]) => n > 0)
    .map(([type, n]) => `${n} ${EXCLUSION_LABEL[type] ?? type}`);
  if (parts.length === 0) return null;
  return (
    <div className="mt-4 rounded-md border border-warn-line bg-warn-soft px-3 py-2 text-sm text-warn">
      Withheld from this package (visible to you only, never to the recipient):{" "}
      {parts.join(" · ")}.
    </div>
  );
}

/** Status chip — encodes lifecycle state in color as well as text. */
function StatusPill({ status }: { status: string }) {
  const map: Record<string, string> = {
    draft: "border-line bg-app2 text-muted",
    generated: "border-brass bg-brass-soft text-brass",
    published: "border-jade bg-jade-soft text-jade",
    revoked: "border-danger-line bg-danger-soft text-danger",
    superseded: "border-line2 bg-app2 text-muted",
  };
  return (
    <span
      className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${map[status] ?? map.draft}`}
    >
      {status}
    </span>
  );
}

/** Full-width package summary bar: title, status/version, editable-or-frozen
 * state, reason, and Start over — the identity of the open package. */
function PackageHeader({
  pkg,
  mutable,
  onStartOver,
}: {
  pkg: HandoffPackage;
  mutable: boolean;
  onStartOver: () => void;
}) {
  return (
    <section className="mt-6 rounded-md border border-line bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-base font-semibold text-ink">
              {pkg.title || "(untitled handoff)"}
            </h3>
            <StatusPill status={pkg.status} />
            <span className="rounded-full border border-line bg-app2 px-2 py-0.5 text-[11px] font-medium text-muted">
              v{pkg.version}
            </span>
            <span
              className={`text-[11px] font-medium ${mutable ? "text-jade" : "text-faint"}`}
            >
              {mutable ? "Editable" : "Frozen"}
            </span>
          </div>
          <div className="mt-1 text-xs text-muted">reason: {pkg.reason}</div>
        </div>
        <button
          type="button"
          className="shrink-0 rounded-md border border-line2 px-3 py-1.5 text-xs font-medium text-muted hover:bg-app2 hover:text-ink"
          onClick={onStartOver}
        >
          Start over
        </button>
      </div>
    </section>
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
    <li className="rounded-md border border-line bg-surface p-3 text-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-semibold text-ink">{ev.subject || "(no subject)"}</div>
          <div className="text-xs text-muted">
            {from || "—"} · {fmtDate(ev.date)}
          </div>
        </div>
        {canRemove ? (
          <button
            type="button"
            className="shrink-0 rounded border border-line px-2 py-1 text-[11px] font-medium text-danger hover:bg-danger-soft disabled:opacity-50"
            onClick={onRemove}
            disabled={disabled}
            title="Exclude this message and regenerate"
          >
            {removing ? "Removing…" : "Remove"}
          </button>
        ) : null}
      </div>
      {ev.body_snapshot ? (
        <p className="mt-2 whitespace-pre-wrap rounded bg-app2 px-2 py-1.5 text-[13px] text-ink">
          {ev.body_snapshot}
        </p>
      ) : null}
      <div className="mt-1 flex items-center gap-2">
        <span className="break-all rounded bg-app2 px-1.5 py-0.5 font-mono text-[10px] text-muted">
          {ev.message_id_header}
        </span>
        <button
          type="button"
          className="rounded border border-line px-2 py-0.5 text-[10px] text-muted hover:bg-app2"
          onClick={onCopy}
        >
          Copy ID
        </button>
      </div>
    </li>
  );
}

/** Minimal claim shape the review needs — satisfied by both HandoffClaim and the
 * coverage-area's RecipientClaim (structurally identical), so no cast is needed. */
interface ReviewClaim {
  id: string;
  kind: string;
  text: string;
  source_message_id_headers: string[];
}

/**
 * One collapsible coverage-area section (S37). Claims are shown first, grouped by
 * kind within the area; the area's evidence is collapsed behind a toggle and
 * disclosed on demand, where each card keeps the existing Remove/Copy pruning.
 * This is a pure presentation grouping over the package snapshot — it never
 * changes which claims/evidence exist, and removal still routes through the same
 * scope-exclusion + regenerate path.
 */
function CoverageAreaSection({
  domId,
  title,
  claims,
  evidence,
  collapsed,
  evidenceOpen,
  onToggleCollapsed,
  onToggleEvidence,
  excludedHeaders,
  onRemove,
  onCopy,
  busy,
  mutable,
}: {
  domId: string;
  title: string;
  claims: ReviewClaim[];
  evidence: HandoffEvidence[];
  collapsed: boolean;
  evidenceOpen: boolean;
  onToggleCollapsed: () => void;
  onToggleEvidence: () => void;
  excludedHeaders: Set<string>;
  onRemove: (header: string) => void;
  onCopy: (header: string) => void;
  busy: boolean;
  mutable: boolean;
}) {
  const byKind = (kind: string) => claims.filter((c) => c.kind === kind);
  const orderedKinds = KIND_ORDER.filter((k) => byKind(k).length > 0);
  const extraKinds = [...new Set(claims.map((c) => c.kind))].filter(
    (k) => !KIND_ORDER.includes(k),
  );
  return (
    <section id={`handoff-${domId}`} className="scroll-mt-4 rounded-md border border-line bg-surface">
      <button
        type="button"
        onClick={onToggleCollapsed}
        aria-expanded={!collapsed}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <span className="w-3 shrink-0 text-xs text-faint" aria-hidden>
          {collapsed ? "▸" : "▾"}
        </span>
        <span className="min-w-0 flex-1 truncate text-sm font-semibold text-ink">
          {title}
        </span>
        <span className="shrink-0 text-[11px] text-muted">
          {claims.length} claim{claims.length === 1 ? "" : "s"} · {evidence.length} evidence
        </span>
      </button>

      {!collapsed ? (
        <div className="border-t border-line px-3 py-2">
          {claims.length === 0 ? (
            <p className="text-xs text-faint">
              Evidence not cited by any claim in this package.
            </p>
          ) : (
            [...orderedKinds, ...extraKinds].map((kind) => (
              <div key={kind} className="mt-2 first:mt-0">
                <div className="text-xs font-semibold uppercase tracking-wide text-faint">
                  {KIND_LABEL[kind] ?? kind}
                </div>
                <ul className="mt-1 space-y-2">
                  {byKind(kind).map((c) => (
                    <li
                      key={c.id}
                      className="rounded-md border border-line bg-app2 px-3 py-2 text-sm"
                    >
                      <div className="text-ink">{c.text}</div>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {c.source_message_id_headers.map((h) => (
                          <span
                            key={h}
                            className="rounded bg-surface px-1.5 py-0.5 font-mono text-[10px] text-muted"
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

          {evidence.length > 0 ? (
            <div className="mt-3">
              <button
                type="button"
                onClick={onToggleEvidence}
                aria-expanded={evidenceOpen}
                className="text-xs font-medium text-brass underline underline-offset-2 hover:text-ink"
              >
                {evidenceOpen
                  ? "Hide evidence"
                  : `Show ${evidence.length} supporting message${evidence.length === 1 ? "" : "s"}`}
              </button>
              {evidenceOpen ? (
                <ul className="mt-2 space-y-3">
                  {evidence.map((ev) => (
                    <EvidenceCard
                      key={ev.message_id_header}
                      ev={ev}
                      removing={excludedHeaders.has(ev.message_id_header)}
                      onRemove={() => onRemove(ev.message_id_header)}
                      onCopy={() => onCopy(ev.message_id_header)}
                      disabled={busy}
                      canRemove={mutable}
                    />
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

/** "Create revised version" — forks a frozen package into a new draft (S17.10).
 * The honest recovery path for a lost link, wrong recipient, or scope revision. */
function ReviseButton({ busy, onClick }: { busy: string | null; onClick: () => void }) {
  return (
    <button
      type="button"
      className="rounded-md border border-line2 bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-app2 disabled:opacity-50"
      onClick={onClick}
      disabled={busy !== null}
    >
      {busy === "version" ? "Creating…" : "Create revised version"}
    </button>
  );
}

/** "Export HTML" — downloads a self-contained static snapshot of a frozen
 * package (S17.11). A plain link the browser downloads; no code is touched. */
function ExportButton({ pkg }: { pkg: HandoffPackage }) {
  const download = () => {
    const a = document.createElement("a");
    a.href = handoffExportUrl(pkg.id);
    a.download = `handoff-package-v${pkg.version}.html`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  };
  return (
    <button
      type="button"
      className="rounded-md border border-line2 bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-app2"
      onClick={download}
    >
      Export HTML
    </button>
  );
}

/**
 * Publish + share-link panel (S17.7, versioning S17.10). States by package status:
 *  - generated  → the publish form (recipient email + expiry, default 30 days).
 *  - published  → the success/share state. If `share` (the transient publish
 *    result) is present, the ONE-TIME recipient link is shown as a copyable field
 *    with a "shown once" warning. On a refresh `share` is gone (never persisted),
 *    so we honestly say the link cannot be recovered — and offer "Create revised
 *    version" as the recovery path.
 *  - revoked / superseded → a terminal notice + "Create revised version".
 *
 * The creator is deliberately given NO "open the link" affordance: the code is
 * one-time, so opening it here would consume it before the recipient can. Copy is
 * the only share action. The raw capability code lives ONLY inside `share`
 * (transient React state) — never written to storage, never logged, and only ever
 * placed in the URL *fragment* of the copyable link.
 */
function PublishControls({
  pkg,
  share,
  busy,
  onPublish,
  onRevoke,
  onNewVersion,
  defaultRecipientEmail,
}: {
  pkg: HandoffPackage;
  share: PublishResponse | null;
  busy: string | null;
  onPublish: (
    recipientEmail: string,
    expiresInDays: number | null,
    safetyAck?: { reason: string; acknowledged_finding_ids: string[] },
  ) => void;
  onRevoke: () => void;
  onNewVersion: () => void;
  defaultRecipientEmail?: string; // S34: prefill return recipient (original creator)
}) {
  const [email, setEmail] = useState(defaultRecipientEmail ?? "");
  const [days, setDays] = useState("30");
  const [copied, setCopied] = useState(false);
  const [overrideOpen, setOverrideOpen] = useState(false);
  const [ackReason, setAckReason] = useState("");

  if (pkg.status === "revoked" || pkg.status === "superseded") {
    const revoked = pkg.status === "revoked";
    return (
      <section className="rounded-md border border-line2 bg-app2 p-4">
        <h3 className="text-sm font-semibold text-ink">
          {revoked ? "Access revoked" : "Replaced by a newer version"}
        </h3>
        <p className="mt-1 text-sm text-muted">
          {revoked
            ? `This handoff was revoked${pkg.revoked_at ? ` on ${fmtDate(pkg.revoked_at)}` : ""}. The recipient can no longer open it.`
            : "A newer version of this handoff has been published. The recipient's access to this version is blocked."}{" "}
          To share again, create a revised version. It starts a fresh draft with
          this package's scope; publishing it mints a new one-time link.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <ReviseButton busy={busy} onClick={onNewVersion} />
          <ExportButton pkg={pkg} />
        </div>
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
      <section className="rounded-md border border-jade bg-jade-soft p-4">
        <h3 className="text-sm font-semibold text-jade">Package published</h3>
        <div className="mt-1 text-xs text-jade">
          {share ? <>Recipient: {share.recipient_email} · </> : null}
          Access expires {fmtDate(pkg.expires_at ?? share?.expires_at ?? "")}
        </div>

        {share && shareUrl ? (
          <div className="mt-3">
            <label className="block text-xs font-medium text-jade">
              Recipient share link
              <input
                type="text"
                readOnly
                value={shareUrl}
                onFocus={(e) => e.currentTarget.select()}
                className="mt-1 w-full rounded border border-jade bg-surface px-2 py-1 font-mono text-xs text-ink"
              />
            </label>
            <div className="mt-2 rounded bg-warn-soft px-2 py-1.5 text-xs text-warn">
              <strong>This link is shown once.</strong> Copy it now and store it
              securely. The code cannot be recovered later, not even by you. The
              code is one-time: it is consumed the first time it is opened, so do
              not open it yourself before sending it to the recipient.
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button
                type="button"
                className="rounded-md bg-jade px-3 py-1.5 text-xs font-medium text-white hover:bg-jade"
                onClick={copy}
              >
                {copied ? "Copied!" : "Copy link"}
              </button>
            </div>
          </div>
        ) : (
          <p className="mt-3 text-xs text-jade">
            The share link was shown once, when this package was published, and
            cannot be recovered from the server. If you need the link again, create
            a revised version below. It starts a fresh draft with this package's
            scope, and publishing it mints a new one-time link (which supersedes
            this version).
          </p>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-jade pt-3">
          <ReviseButton busy={busy} onClick={onNewVersion} />
          <ExportButton pkg={pkg} />
          <button
            type="button"
            className="text-xs font-medium text-danger hover:text-danger disabled:opacity-50"
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
  const expiresDays = Number.isFinite(parsedDays) && parsedDays > 0 ? parsedDays : null;
  const highFindings = (pkg.findings ?? []).filter((f) => f.severity === "high");
  const submit = () => {
    // High-severity findings must be acknowledged with a reason (S44). Open the
    // override panel instead of publishing; the creator can also just prune the
    // flagged content and regenerate to clear them.
    if (highFindings.length > 0) {
      setOverrideOpen(true);
      return;
    }
    onPublish(email.trim(), expiresDays);
  };
  const confirmOverride = () => {
    onPublish(email.trim(), expiresDays, {
      reason: ackReason.trim(),
      acknowledged_finding_ids: highFindings.map((f) => f.id),
    });
    setOverrideOpen(false);
  };

  return (
    <section className="mt-6 rounded-md border border-brass bg-brass-soft p-4">
      <h3 className="text-sm font-semibold text-ink">Publish package</h3>
      <p className="mt-1 text-xs text-ink">
        Publishing freezes this package and generates a one-time recipient link.
        After publishing you can no longer edit scope, regenerate, or remove
        evidence.
      </p>

      {highFindings.length > 0 ? (
        <div className="mt-3 rounded-md border border-danger-line bg-danger-soft px-3 py-2 text-xs text-danger">
          <strong>
            {highFindings.length} high-severity safety finding
            {highFindings.length === 1 ? "" : "s"} block publishing.
          </strong>{" "}
          Remove the flagged content and regenerate, or acknowledge with a reason to
          publish anyway.
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="flex flex-1 flex-col text-xs text-ink">
          Recipient email
          <input
            type="email"
            className="mt-1 rounded border border-brass bg-surface px-2 py-1 text-sm text-ink"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="cover@company.com"
          />
        </label>
        <label className="flex w-28 flex-col text-xs text-ink">
          Expires (days)
          <input
            type="number"
            min={1}
            max={365}
            className="mt-1 rounded border border-brass bg-surface px-2 py-1 text-sm text-ink"
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
        </label>
        <button
          type="button"
          className="rounded-md bg-brass px-4 py-2 text-sm font-medium text-onbrass hover:bg-brass disabled:bg-brass-soft disabled:text-faint"
          onClick={submit}
          disabled={busy !== null || !email.trim() || noEvidence}
          title={noEvidence ? "Generate a package with evidence before publishing" : undefined}
        >
          {busy === "publish"
            ? "Publishing…"
            : highFindings.length > 0
              ? "Review safety findings…"
              : "Publish package"}
        </button>
      </div>
      {noEvidence ? (
        <p className="mt-2 text-xs text-brass">
          This package has no evidence yet. Widen the scope and regenerate before
          publishing.
        </p>
      ) : null}

      {overrideOpen ? (
        <div className="mt-3 rounded-md border border-danger-line bg-surface p-3">
          <div className="text-xs font-semibold text-danger">
            Acknowledge {highFindings.length} high-severity finding
            {highFindings.length === 1 ? "" : "s"} to publish
          </div>
          <ul className="mt-2 space-y-1">
            {highFindings.map((f) => (
              <li key={f.id} className="text-xs text-ink">
                <span className="font-mono text-[11px] text-danger">{f.category}</span>{" "}
                {f.explanation}
              </li>
            ))}
          </ul>
          <label className="mt-2 block text-xs text-ink">
            Reason (recorded in the audit trail)
            <textarea
              rows={2}
              maxLength={500}
              value={ackReason}
              onChange={(e) => setAckReason(e.target.value)}
              className="mt-1 w-full rounded border border-line2 bg-surface px-2 py-1 text-sm text-ink"
              placeholder="Why is it safe to publish these?"
            />
          </label>
          <div className="mt-2 flex items-center gap-3">
            <button
              type="button"
              className="rounded-md bg-danger px-3 py-1.5 text-xs font-medium text-white hover:bg-danger disabled:opacity-50"
              onClick={confirmOverride}
              disabled={busy !== null || !ackReason.trim() || !email.trim()}
            >
              {busy === "publish" ? "Publishing…" : "Publish with override"}
            </button>
            <button
              type="button"
              className="text-xs font-medium text-muted hover:text-ink"
              onClick={() => setOverrideOpen(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
