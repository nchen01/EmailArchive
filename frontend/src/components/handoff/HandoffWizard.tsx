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
} from "../../api/client";
import type {
  CoverageContractEntry,
  HandoffPackage,
  HandoffScopeData,
  ProjectSummary,
  PublishResponse,
  ReturnContext,
  SafetyFinding,
} from "../../api/types";
import { navigate, usePathname } from "../../router";
import { useProjects } from "../../hooks/useProjects";
import {
  buildHandoffProjectGroups,
  filterHandoffGroup,
  type HandoffGroup,
} from "../../utils/handoffGroups";
import { ReturnBanner, ReturnCreatePanel } from "../ReturnHandoff";
import {
  CoverageAreaSection,
  ExclusionSummary,
  SafetyReviewPanel,
  generationEmptyMessage,
  HandoffReview,
} from "../HandoffReview";

/**
 * Creator Guided Handoff Wizard (S46, spec docs/s45-creator-guided-handoff-wizard-plan.md).
 *
 * A frontend-only, wizard-first reframing of the SAME creator flow the detailed
 * HandoffReview already drives: create -> scope -> generate/review/prune ->
 * safety -> publish. It reuses the existing creator endpoints (via the api client),
 * the S37 project grouping, the S34 return-handoff entry/banner, and the S44
 * safety findings/gate. It adds NO backend, schema, recipient, or admin change.
 *
 * The old detailed surface is preserved verbatim as an "Advanced / full evidence
 * review" mode: the wizard mounts the unchanged HandoffReview when the creator
 * flips into that mode, then refetches the package on return so a prune/regenerate
 * done there is reflected in the guided steps.
 *
 * Every privacy invariant is inherited: the creator reasons only over their own
 * package DTO, the recipient stays snapshot-only (nothing here touches recipient
 * routes), findings render as safe metadata only, and the one-time capability code
 * lives ONLY in transient React state (never persisted/logged). The server publish
 * gate remains the source of truth; the wizard only prevents avoidable errors.
 */

const HANDOFF_BASE = "/app/handoff";
const REASONS = ["vacation", "leave", "transfer", "delegation", "other"];

// Above this the client-side breadth guidance flags a scope as broad. Purely
// advisory (never blocks); computed from already-loaded project thread counts.
const BROAD_PROJECT_COUNT = 6;
const BROAD_THREAD_COUNT = 40;

type Step = "start" | "scope" | "review" | "safety" | "publish" | "done";

const STEP_LABELS: { key: Step; label: string }[] = [
  { key: "start", label: "Start" },
  { key: "scope", label: "Scope" },
  { key: "review", label: "Review" },
  { key: "safety", label: "Safety" },
  { key: "publish", label: "Publish" },
];

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/** The COMPLETE current scope as a replace-like PATCH body (backend scope PATCH is
 * replace-like: any omitted array resets to empty, so we always send the whole
 * scope, preserving seeded person/thread/excluded sets). */
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

export function HandoffWizard({ mailboxId }: { mailboxId: string }) {
  const pathname = usePathname();
  // The URL is the source of truth for which package is open, so a package
  // survives tab switches and refreshes: /app/handoff/<id>.
  const routeId = pathname.startsWith(`${HANDOFF_BASE}/`)
    ? decodeURIComponent(pathname.slice(HANDOFF_BASE.length + 1))
    : null;
  // S34: routed from the recipient view's "Create return handoff" via
  // /app/handoff?return_from=<original_package_id>.
  const returnFrom = pathname === HANDOFF_BASE
    ? new URLSearchParams(window.location.search).get("return_from")
    : null;

  const [pkg, setPkg] = useState<HandoffPackage | null>(null);
  const [returnCtx, setReturnCtx] = useState<ReturnContext | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<
    null | "create" | "scope" | "generate" | "remove" | "restore" | "publish" | "revoke" | "version"
  >(null);
  // "advanced" mounts the unchanged detailed HandoffReview; a local toggle, not a
  // URL/route, so it never survives refresh (the guided flow is the primary path).
  const [viewMode, setViewMode] = useState<"guided" | "advanced">("guided");

  // Wizard step. Derived/clamped from the package status by the effect below, and
  // moved forward/back by the step buttons. `start` when no package is open.
  const [step, setStep] = useState<Step>("start");

  // Create form (forward handoff).
  const [reason, setReason] = useState("vacation");
  const [title, setTitle] = useState("");

  // Scope form. Person/thread scope is seeded from the package (e.g. a return
  // handoff auto-seed) and is EDITABLE as removable chips here; adding brand-new
  // contacts/threads is deferred to the Advanced surface (no creator-safe list
  // endpoint is wired for the guided step). See docs/s45 S46 status note.
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [selectedProjects, setSelectedProjects] = useState<Set<string>>(() => new Set());
  const [selectedPersons, setSelectedPersons] = useState<Set<string>>(() => new Set());
  const [selectedThreads, setSelectedThreads] = useState<Set<string>>(() => new Set());

  // Review filter / disclosure.
  const [reviewFilter, setReviewFilter] = useState("");
  const [collapsedAreas, setCollapsedAreas] = useState<Set<string>>(() => new Set());
  const [openEvidence, setOpenEvidence] = useState<Set<string>>(() => new Set());

  // Safety acknowledgement (S44). `acked` is the set of high finding ids the
  // creator has acknowledged; publish is only unblocked when it covers EVERY
  // current high finding id and a reason is present. Regeneration changes the
  // finding ids, so a stale ack no longer satisfies the gate.
  const [ackReason, setAckReason] = useState("");
  const [acked, setAcked] = useState<Set<string>>(() => new Set());

  // Publish form + transient one-time share result (held in state ONLY).
  const [email, setEmail] = useState("");
  const [days, setDays] = useState("30");
  const [share, setShare] = useState<PublishResponse | null>(null);

  const { projects } = useProjects(mailboxId);
  const labelById = useMemo(
    () => new Map(projects.map((p) => [p.id, p.label])),
    [projects],
  );

  // Load / validate the package named in the URL for the current mailbox. Mirrors
  // HandoffReview: self-heals a route that points at a missing package or another
  // mailbox's package by returning to the base (create/start) route.
  useEffect(() => {
    let cancelled = false;
    if (!routeId) {
      setPkg(null);
      setLoading(false);
      setError(null);
      return;
    }
    if (pkg && pkg.id === routeId && pkg.mailbox_id === mailboxId) return;
    setError(null);
    setLoading(true);
    getHandoff(routeId)
      .then((data) => {
        if (cancelled) return;
        if (data.mailbox_id !== mailboxId) {
          setPkg(null);
          navigate(HANDOFF_BASE);
        } else {
          setPkg(data);
        }
      })
      .catch(() => {
        if (cancelled) return;
        setPkg(null);
        navigate(HANDOFF_BASE);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeId, mailboxId, pkg?.id]);

  // Sync per-package state on a package change (keyed on id so a same-package
  // status change, e.g. publish, does NOT clear the transient share result).
  useEffect(() => {
    setShare(null);
    setAcked(new Set());
    setAckReason("");
    setReviewFilter("");
    setViewMode("guided");
    if (pkg) {
      setFrom(pkg.scope.date_from ?? "");
      setTo(pkg.scope.date_to ?? "");
      setSelectedProjects(new Set(pkg.scope.included_project_ids));
      setSelectedPersons(new Set(pkg.scope.included_person_ids));
      setSelectedThreads(new Set(pkg.scope.included_thread_ids));
      setEmail("");
    } else {
      setReason("vacation");
      setTitle("");
      setFrom("");
      setTo("");
      setSelectedProjects(new Set());
      setSelectedPersons(new Set());
      setSelectedThreads(new Set());
      setEmail("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pkg?.id]);

  // S34: fetch the safe return context for return_delta packages (framing +
  // suggested recipient).
  useEffect(() => {
    if (pkg && pkg.package_type === "return_delta") {
      getReturnContext(pkg.id)
        .then(setReturnCtx)
        .catch(() => setReturnCtx(null));
    } else {
      setReturnCtx(null);
    }
  }, [pkg?.id, pkg?.package_type]);

  // Clamp the wizard step to what the package status allows. Keeps the flow
  // resumable across refresh: a draft resumes at Scope, a generated package at
  // Review (unless the creator already advanced), a frozen package at the Done
  // summary. Reset disclosure when the claim/evidence set changes.
  useEffect(() => {
    if (!pkg) {
      setStep("start");
      return;
    }
    if (pkg.status === "published" || pkg.status === "revoked" || pkg.status === "superseded") {
      setStep("done");
      return;
    }
    if (pkg.status === "draft") {
      setStep("scope");
      return;
    }
    // generated
    setStep((s) => (s === "scope" || s === "review" || s === "safety" || s === "publish" ? s : "review"));
    setCollapsedAreas(new Set());
    setOpenEvidence(new Set());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pkg?.id, pkg?.status, pkg?.updated_at]);

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
      navigate(`${HANDOFF_BASE}/${p.id}`);
      return p;
    });

  // Persist the current scope inputs (date range + selected projects), preserving
  // any seeded person/thread/excluded sets already on the package.
  const applyScope = (): Promise<HandoffPackage> => {
    if (!pkg) return Promise.reject(new Error("no package"));
    return updateHandoffScope(pkg.id, {
      ...scopeBody(pkg),
      date_from: from || null,
      date_to: to || null,
      included_project_ids: [...selectedProjects],
      included_person_ids: [...selectedPersons],
      included_thread_ids: [...selectedThreads],
    });
  };

  const saveScope = () => pkg && run("scope", applyScope);

  // Generate always saves the current scope first so the creator's project/date
  // selection is applied, then builds the candidate and advances to Review. The
  // step is set explicitly because the clamp effect intentionally keeps a valid
  // "scope" step (the Adjust-scope path) rather than forcing Review on its own.
  const generate = async () => {
    if (!pkg) return;
    setBusy("generate");
    setError(null);
    try {
      await applyScope();
      const g = await generateHandoff(pkg.id);
      setPkg(g);
      setStep("review");
    } catch (e) {
      setError(describeError(e).message);
    } finally {
      setBusy(null);
    }
  };

  const removeEvidence = (header: string) =>
    pkg &&
    run("remove", async () => {
      const excluded = [...pkg.scope.excluded_message_id_headers, header];
      await updateHandoffScope(pkg.id, { ...scopeBody(pkg), excluded_message_id_headers: excluded });
      return generateHandoff(pkg.id);
    });

  const restoreAllEvidence = () =>
    pkg &&
    run("restore", async () => {
      await updateHandoffScope(pkg.id, { ...scopeBody(pkg), excluded_message_id_headers: [] });
      return generateHandoff(pkg.id);
    });

  const publish = async () => {
    if (!pkg) return;
    setBusy("publish");
    setError(null);
    try {
      const resp = await publishHandoff(pkg.id, {
        recipient_email: email.trim(),
        ...(expiresDays ? { expires_in_days: expiresDays } : {}),
        ...(highFindings.length > 0
          ? { safety_ack: { reason: ackReason.trim(), acknowledged_finding_ids: highFindings.map((f) => f.id) } }
          : {}),
      });
      setPkg(resp.package);
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
        "Revoke this handoff? The recipient's access is blocked immediately and cannot be restored from here.",
      )
    )
      return;
    setBusy("revoke");
    setError(null);
    try {
      const updated = await revokeHandoff(pkg.id);
      setPkg(updated);
      setShare(null);
    } catch (e) {
      setError(describeError(e).message);
    } finally {
      setBusy(null);
    }
  };

  const newVersion = async () => {
    if (!pkg) return;
    setBusy("version");
    setError(null);
    try {
      const draft = await newVersionHandoff(pkg.id);
      navigate(`${HANDOFF_BASE}/${draft.id}`);
    } catch (e) {
      setError(describeError(e).message);
    } finally {
      setBusy(null);
    }
  };

  const copyId = (value: string) => {
    navigator.clipboard?.writeText(value).catch(() => window.prompt("Copy this Message-ID:", value));
  };

  const backToGuided = () => {
    setViewMode("guided");
    // Advanced mode may have pruned/regenerated; refetch so guided steps reflect it.
    if (pkg) getHandoff(pkg.id).then(setPkg).catch(() => undefined);
  };

  // ---- derivations -------------------------------------------------------
  const mutable = !pkg || pkg.status === "draft" || pkg.status === "generated";
  const groups = useMemo<HandoffGroup[]>(
    () =>
      pkg && pkg.claims.length > 0
        ? buildHandoffProjectGroups(pkg.claims, pkg.evidence, labelById)
        : [],
    [pkg?.id, pkg?.claims, pkg?.evidence, labelById],
  );
  const q = reviewFilter.trim().toLowerCase();
  const visibleGroups = groups
    .map((g) => ({ group: g, ...filterHandoffGroup(g, q) }))
    .filter((fg) => fg.claims.length > 0 || fg.evidence.length > 0);
  const excludedHeaders = new Set(pkg?.scope.excluded_message_id_headers ?? []);
  const excludedCount = excludedHeaders.size;

  const highFindings = (pkg?.findings ?? []).filter((f) => f.severity === "high");
  const highIds = highFindings.map((f) => f.id);
  const allHighAcked = highIds.length > 0 && highIds.every((id) => acked.has(id));
  const safetySatisfied = highFindings.length === 0 || (allHighAcked && ackReason.trim().length > 0);

  const parsedDays = parseInt(days, 10);
  const expiresDays = Number.isFinite(parsedDays) && parsedDays > 0 ? parsedDays : null;

  // Client-side breadth guidance from already-loaded project data (no endpoint).
  const selectedThreadEstimate = projects
    .filter((p) => selectedProjects.has(p.id))
    .reduce((n, p) => n + (p.thread_count || 0), 0);
  // Scope is "empty" only when nothing at all is selected: no project, contact,
  // thread, or date bound. Empty scope disables Generate (spec: at least one
  // project/person/thread/date range is required; there is no whole-mailbox
  // default in the guided path).
  const anyScope =
    selectedProjects.size > 0 ||
    selectedPersons.size > 0 ||
    selectedThreads.size > 0 ||
    !!from ||
    !!to;
  // "Broad" is a non-blocking caution, only meaningful once something IS scoped.
  const broadScope =
    anyScope &&
    (selectedProjects.size > BROAD_PROJECT_COUNT || selectedThreadEstimate > BROAD_THREAD_COUNT);

  const toggleProject = (id: string) =>
    setSelectedProjects((p) => {
      const n = new Set(p);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const removePerson = (id: string) =>
    setSelectedPersons((prev) => {
      const n = new Set(prev);
      n.delete(id);
      return n;
    });
  const removeThread = (id: string) =>
    setSelectedThreads((prev) => {
      const n = new Set(prev);
      n.delete(id);
      return n;
    });

  const acknowledgeHighFindings = () => {
    setAcked(new Set(highIds));
  };

  // ---- advanced mode: the unchanged detailed review surface --------------
  if (viewMode === "advanced" && pkg) {
    return (
      <div className="mx-auto w-full max-w-[92rem] px-4 py-4 sm:px-6">
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-line bg-app2 px-3 py-2">
          <div className="text-xs text-muted">
            <strong className="text-ink">Advanced / full evidence review.</strong> The detailed
            review surface. Changes here (prune, regenerate, publish) apply to the same package.
          </div>
          <button
            type="button"
            className="rounded-md border border-line2 bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-app2"
            onClick={backToGuided}
          >
            Back to guided review
          </button>
        </div>
        <HandoffReview mailboxId={mailboxId} />
      </div>
    );
  }

  // ---- guided shell ------------------------------------------------------
  const currentIndex = STEP_LABELS.findIndex((s) => s.key === (step === "done" ? "publish" : step));

  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-6 sm:px-6">
      <h2 className="text-lg font-semibold text-ink">Create a handoff package</h2>
      <p className="mt-1 max-w-2xl text-sm text-muted">
        A guided flow to scope, review, safety-check, and publish a coverage handoff
        from <strong>your own mailbox</strong>. The recipient only ever sees the frozen,
        reviewed package you publish, never your live mailbox.
      </p>

      <Stepper currentIndex={currentIndex} pkg={pkg} />

      {error ? (
        <div className="status-failed mt-4" role="alert">
          {error}
        </div>
      ) : null}

      {loading && !pkg ? (
        <p className="mt-6 text-sm text-muted">Loading handoff package...</p>
      ) : step === "start" ? (
        <StartStep
          reason={reason}
          title={title}
          busy={busy}
          onReason={setReason}
          onTitle={setTitle}
          onCreate={create}
          mailboxId={mailboxId}
          returnFrom={returnFrom}
        />
      ) : !pkg ? null : (
        <>
          {pkg.package_type === "return_delta" ? <ReturnBanner ctx={returnCtx} pkg={pkg} /> : null}

          {step === "scope" ? (
            <ScopeStep
              pkg={pkg}
              projects={projects}
              selectedProjects={selectedProjects}
              selectedPersons={selectedPersons}
              selectedThreads={selectedThreads}
              from={from}
              to={to}
              busy={busy}
              broadScope={broadScope}
              anyScope={anyScope}
              selectedThreadEstimate={selectedThreadEstimate}
              onToggleProject={toggleProject}
              onRemovePerson={removePerson}
              onRemoveThread={removeThread}
              onFrom={setFrom}
              onTo={setTo}
              onSaveScope={saveScope}
              onGenerate={generate}
            />
          ) : null}

          {step === "review" || step === "safety" || step === "publish" ? (
            <>
              {step === "review" ? (
                <ReviewStep
                  pkg={pkg}
                  groups={groups}
                  visibleGroups={visibleGroups}
                  reviewFilter={reviewFilter}
                  onFilter={setReviewFilter}
                  excludedHeaders={excludedHeaders}
                  excludedCount={excludedCount}
                  collapsedAreas={collapsedAreas}
                  openEvidence={openEvidence}
                  onToggleCollapsed={(id) =>
                    setCollapsedAreas((p) => {
                      const n = new Set(p);
                      if (n.has(id)) n.delete(id);
                      else n.add(id);
                      return n;
                    })
                  }
                  onToggleEvidence={(id) =>
                    setOpenEvidence((p) => {
                      const n = new Set(p);
                      if (n.has(id)) n.delete(id);
                      else n.add(id);
                      return n;
                    })
                  }
                  onRemove={removeEvidence}
                  onRestoreAll={restoreAllEvidence}
                  onCopy={copyId}
                  busy={busy}
                  mutable={mutable}
                  onAdjustScope={() => setStep("scope")}
                  onRegenerate={generate}
                  onAdvanced={() => setViewMode("advanced")}
                  onContinue={() => setStep("safety")}
                />
              ) : null}

              {step === "safety" ? (
                <SafetyStep
                  findings={pkg.findings ?? []}
                  highFindings={highFindings}
                  ackReason={ackReason}
                  onAckReason={setAckReason}
                  acknowledged={allHighAcked}
                  onAcknowledge={acknowledgeHighFindings}
                  satisfied={safetySatisfied}
                  onBackToReview={() => setStep("review")}
                  onContinue={() => setStep("publish")}
                />
              ) : null}

              {step === "publish" ? (
                <PublishStep
                  pkg={pkg}
                  email={email}
                  days={days}
                  onEmail={setEmail}
                  onDays={setDays}
                  excludedCount={excludedCount}
                  highFindings={highFindings}
                  safetySatisfied={safetySatisfied}
                  busy={busy}
                  isReturn={pkg.package_type === "return_delta"}
                  suggestedRecipient={
                    pkg.package_type === "return_delta"
                      ? returnCtx?.suggested_recipient_email
                      : undefined
                  }
                  onBack={() => setStep("safety")}
                  onPublish={publish}
                />
              ) : null}
            </>
          ) : null}

          {step === "done" ? (
            <DoneStep
              pkg={pkg}
              share={share}
              busy={busy}
              onRevoke={revoke}
              onNewVersion={newVersion}
              onStartOver={() => navigate(HANDOFF_BASE)}
            />
          ) : null}
        </>
      )}
    </div>
  );
}

/** Horizontal step indicator. A frozen (published/revoked) package shows the last
 * step as done. */
function Stepper({ currentIndex, pkg }: { currentIndex: number; pkg: HandoffPackage | null }) {
  const frozen =
    pkg?.status === "published" || pkg?.status === "revoked" || pkg?.status === "superseded";
  return (
    <ol className="mt-4 flex flex-wrap items-center gap-1 text-xs" aria-label="Handoff steps">
      {STEP_LABELS.map((s, i) => {
        const done = i < currentIndex || (frozen && i <= currentIndex);
        const active = i === currentIndex && !frozen;
        return (
          <li key={s.key} className="flex items-center gap-1">
            <span
              className={
                "flex h-6 min-w-6 items-center justify-center rounded-full border px-2 font-semibold " +
                (active
                  ? "border-brass bg-brass-soft text-brass"
                  : done
                    ? "border-jade bg-jade-soft text-jade"
                    : "border-line bg-app2 text-faint")
              }
            >
              {done ? "OK" : i + 1}
            </span>
            <span className={active ? "font-medium text-ink" : "text-muted"}>{s.label}</span>
            {i < STEP_LABELS.length - 1 ? (
              <span className="px-1 text-faint" aria-hidden>
                -&gt;
              </span>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

/** Step 1: choose forward or return handoff and create the draft. */
function StartStep({
  reason,
  title,
  busy,
  onReason,
  onTitle,
  onCreate,
  mailboxId,
  returnFrom,
}: {
  reason: string;
  title: string;
  busy: string | null;
  onReason: (v: string) => void;
  onTitle: (v: string) => void;
  onCreate: () => void;
  mailboxId: string;
  returnFrom: string | null;
}) {
  return (
    <section className="mt-4 rounded-md border border-line bg-surface p-4">
      <h3 className="text-sm font-semibold text-ink">Start a forward handoff</h3>
      <p className="mt-1 text-xs text-muted">
        Covering your work while you are away. The package is built from your own mailbox and
        published to one recipient.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="flex flex-col text-xs text-muted">
          Reason
          <select
            className="mt-1 rounded border border-line2 px-2 py-1 text-sm"
            value={reason}
            onChange={(e) => onReason(e.target.value)}
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
            onChange={(e) => onTitle(e.target.value)}
            placeholder="e.g. Covering Atlas while I am out"
          />
        </label>
        <button
          type="button"
          className="rounded-md bg-brass px-4 py-2 text-sm font-medium text-onbrass hover:bg-brass disabled:bg-brass-soft disabled:text-faint"
          onClick={onCreate}
          disabled={busy !== null}
        >
          {busy === "create" ? "Creating..." : "Create draft & continue"}
        </button>
      </div>

      <div className="mt-4 border-t border-line pt-3">
        <p className="text-xs text-muted">
          Covered someone else while they were away? Start a{" "}
          <strong>return handoff</strong> instead. It is a reciprocal package built from your
          mailbox and sent back to the original employee, and it enters the same review, safety,
          and publish steps.
        </p>
        <ReturnCreatePanel mailboxId={mailboxId} initialOriginalId={returnFrom ?? undefined} />
      </div>
    </section>
  );
}

/** Step 2: date range + project + (seeded) person/thread scope. Empty scope
 * disables Generate with a reason; a broad selection gets a non-blocking caution.
 * Person/thread scope is seeded and removable here (adding new is Advanced). */
function ScopeStep({
  pkg,
  projects,
  selectedProjects,
  selectedPersons,
  selectedThreads,
  from,
  to,
  busy,
  broadScope,
  anyScope,
  selectedThreadEstimate,
  onToggleProject,
  onRemovePerson,
  onRemoveThread,
  onFrom,
  onTo,
  onSaveScope,
  onGenerate,
}: {
  pkg: HandoffPackage;
  projects: ProjectSummary[];
  selectedProjects: Set<string>;
  selectedPersons: Set<string>;
  selectedThreads: Set<string>;
  from: string;
  to: string;
  busy: string | null;
  broadScope: boolean;
  anyScope: boolean;
  selectedThreadEstimate: number;
  onToggleProject: (id: string) => void;
  onRemovePerson: (id: string) => void;
  onRemoveThread: (id: string) => void;
  onFrom: (v: string) => void;
  onTo: (v: string) => void;
  onSaveScope: () => void;
  onGenerate: () => void;
}) {
  const generating = busy === "generate" || busy === "scope";
  const alreadyGenerated = pkg.status === "generated";
  const personChips = [...selectedPersons];
  const threadChips = [...selectedThreads];
  return (
    <section className="mt-4 rounded-md border border-line bg-surface p-4">
      <h3 className="text-sm font-semibold text-ink">Scope the handoff</h3>
      <p className="mt-1 text-xs text-muted">
        Narrow what the package will draw from. A tighter scope is easier to review and less likely
        to include something that should not travel. You will still review and prune everything
        before publishing.
      </p>

      <div className="mt-3 flex flex-wrap gap-3">
        <label className="flex flex-col text-xs text-muted">
          From
          <input
            type="date"
            className="mt-1 rounded border border-line2 bg-surface px-2 py-1 text-sm text-ink"
            value={from}
            onChange={(e) => onFrom(e.target.value)}
          />
        </label>
        <label className="flex flex-col text-xs text-muted">
          To
          <input
            type="date"
            className="mt-1 rounded border border-line2 bg-surface px-2 py-1 text-sm text-ink"
            value={to}
            onChange={(e) => onTo(e.target.value)}
          />
        </label>
      </div>

      <div className="mt-4">
        <div className="text-xs font-semibold uppercase tracking-wide text-faint">Projects</div>
        {projects.length === 0 ? (
          <p className="mt-1 text-xs text-muted">
            No project list is available for this mailbox. Scope by date range (or, for a return
            handoff, by the seeded contacts/threads below).
          </p>
        ) : (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {projects.map((p) => {
              const on = selectedProjects.has(p.id);
              return (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => onToggleProject(p.id)}
                  className={
                    "max-w-[20rem] truncate rounded-full border px-2.5 py-1 text-[11px] " +
                    (on
                      ? "border-brass bg-brass-soft text-brass"
                      : "border-line text-muted hover:bg-app2 hover:text-ink")
                  }
                  title={`${p.label} (${p.thread_count} thread${p.thread_count === 1 ? "" : "s"})`}
                >
                  {on ? "x " : "+ "}
                  {p.label} - {p.thread_count}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {personChips.length > 0 || threadChips.length > 0 ? (
        <div className="mt-4">
          <div className="text-xs font-semibold uppercase tracking-wide text-faint">
            Contacts &amp; threads (seeded)
          </div>
          <p className="mt-1 text-[11px] text-faint">
            Seeded from this package (e.g. a return handoff auto-seed). Remove any you do not want in
            scope. Adding new contacts or threads is available in Advanced review.
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {personChips.map((id) => (
              <button
                key={`person-${id}`}
                type="button"
                onClick={() => onRemovePerson(id)}
                className="max-w-[16rem] truncate rounded-full border border-brass bg-brass-soft px-2.5 py-1 text-[11px] text-brass hover:bg-app2"
                title={`Remove contact ${id}`}
              >
                x contact {id.slice(0, 8)}
              </button>
            ))}
            {threadChips.map((id) => (
              <button
                key={`thread-${id}`}
                type="button"
                onClick={() => onRemoveThread(id)}
                className="max-w-[16rem] truncate rounded-full border border-brass bg-brass-soft px-2.5 py-1 text-[11px] text-brass hover:bg-app2"
                title={`Remove thread ${id}`}
              >
                x thread {id.slice(0, 8)}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {!anyScope ? (
        <div className="mt-3 rounded-md border border-warn-line bg-warn-soft px-3 py-2 text-xs text-warn">
          <strong>Choose a scope before generating.</strong> Select at least one project, a seeded
          contact/thread, or a date range. The guided flow deliberately does not generate from your
          entire mailbox; a scoped package is faster to review and safer to share.
        </div>
      ) : broadScope ? (
        <div className="mt-3 rounded-md border border-warn-line bg-warn-soft px-3 py-2 text-xs text-warn">
          <strong>This looks broad.</strong> You selected {selectedProjects.size} project(s)
          {selectedThreadEstimate > 0 ? ` (about ${selectedThreadEstimate} threads)` : ""}. You will
          review and prune everything it selects, so a tighter scope is usually easier.
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="rounded-md border border-line2 px-3 py-2 text-sm text-ink hover:bg-app2 disabled:opacity-50"
          onClick={onSaveScope}
          disabled={busy !== null}
        >
          {busy === "scope" ? "Saving..." : "Save scope"}
        </button>
        <button
          type="button"
          className="rounded-md bg-brass px-4 py-2 text-sm font-medium text-onbrass hover:bg-brass disabled:bg-brass-soft disabled:text-faint"
          onClick={onGenerate}
          disabled={busy !== null || !anyScope}
          title={!anyScope ? "Select a project, contact/thread, or date range first" : undefined}
        >
          {generating ? "Generating..." : alreadyGenerated ? "Regenerate & review" : "Generate & review"}
        </button>
      </div>
    </section>
  );
}

/** Step 3: generated claims grouped by project, collapsed evidence, prune. */
function ReviewStep({
  pkg,
  groups,
  visibleGroups,
  reviewFilter,
  onFilter,
  excludedHeaders,
  excludedCount,
  collapsedAreas,
  openEvidence,
  onToggleCollapsed,
  onToggleEvidence,
  onRemove,
  onRestoreAll,
  onCopy,
  busy,
  mutable,
  onAdjustScope,
  onRegenerate,
  onAdvanced,
  onContinue,
}: {
  pkg: HandoffPackage;
  groups: HandoffGroup[];
  visibleGroups: { group: HandoffGroup; claims: HandoffGroup["claims"]; evidence: HandoffGroup["evidence"] }[];
  reviewFilter: string;
  onFilter: (v: string) => void;
  excludedHeaders: Set<string>;
  excludedCount: number;
  collapsedAreas: Set<string>;
  openEvidence: Set<string>;
  onToggleCollapsed: (id: string) => void;
  onToggleEvidence: (id: string) => void;
  onRemove: (header: string) => void;
  onRestoreAll: () => void;
  onCopy: (header: string) => void;
  busy: string | null;
  mutable: boolean;
  onAdjustScope: () => void;
  onRegenerate: () => void;
  onAdvanced: () => void;
  onContinue: () => void;
}) {
  const empty = pkg.claims.length === 0;
  return (
    <section className="mt-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">
          Review the candidate - {pkg.claims.length} claim{pkg.claims.length === 1 ? "" : "s"} /{" "}
          {pkg.evidence.length} evidence
        </h3>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="rounded-md border border-line2 bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-app2 disabled:opacity-50"
            onClick={onAdjustScope}
            disabled={busy !== null || !mutable}
          >
            Adjust scope
          </button>
          <button
            type="button"
            className="rounded-md border border-line2 bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-app2"
            onClick={onAdvanced}
            title="Open the detailed full-evidence review surface"
          >
            Advanced review
          </button>
        </div>
      </div>

      <ExclusionSummary counts={pkg.exclusion_counts} />
      <SafetyReviewPanel findings={pkg.findings ?? []} />
      <CoverageContractSummary
        entries={pkg.coverage_contract ?? []}
        onSelectProject={(label) => {
          onFilter(label);
          requestAnimationFrame(() =>
            document
              .getElementById("wizard-review-groups")
              ?.scrollIntoView({ behavior: "smooth", block: "start" }),
          );
        }}
      />

      {empty ? (
        <div className="mt-4 rounded-md border border-warn-line bg-warn-soft px-3 py-2 text-sm text-warn">
          <p>{generationEmptyMessage(pkg)}</p>
          <button
            type="button"
            className="mt-2 rounded-md border border-warn-line bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-app2 disabled:opacity-50"
            onClick={onAdjustScope}
            disabled={busy !== null}
          >
            Widen scope
          </button>
        </div>
      ) : (
        <>
          <div id="wizard-review-groups" className="mt-3 scroll-mt-4 rounded-md border border-line bg-surface p-3">
            <input
              type="text"
              value={reviewFilter}
              onChange={(e) => onFilter(e.target.value)}
              placeholder="Filter by project, claim, subject, or sender..."
              className="w-full rounded border border-line2 bg-surface px-2 py-1 text-sm text-ink"
              aria-label="Filter project groups"
            />
            <p className="mt-2 text-[11px] text-faint">
              Grouped into {groups.length} project group{groups.length === 1 ? "" : "s"}. Evidence is
              collapsed under each group; expand to inspect or remove. A claim left without evidence
              disappears on regenerate.
            </p>
          </div>

          {excludedCount > 0 ? (
            <div className="mt-3 rounded-md border border-warn-line bg-warn-soft px-3 py-2 text-xs text-warn">
              <div className="font-medium">
                {excludedCount} evidence item{excludedCount === 1 ? "" : "s"} removed by you.
              </div>
              <p className="mt-1">
                Removed evidence stays excluded when you regenerate. Sensitive/noise content is
                excluded by policy separately and is never restored here.
              </p>
              {mutable ? (
                <button
                  type="button"
                  className="mt-2 rounded-md border border-warn-line bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-app2 disabled:opacity-50"
                  onClick={onRestoreAll}
                  disabled={busy !== null}
                >
                  {busy === "restore" ? "Restoring..." : "Restore all removed evidence"}
                </button>
              ) : null}
            </div>
          ) : null}

          <div className="mt-3 space-y-3">
            {visibleGroups.map((fg) => (
              <CoverageAreaSection
                key={fg.group.id}
                domId={fg.group.id}
                title={fg.group.label}
                claims={fg.claims}
                evidence={fg.evidence}
                collapsed={collapsedAreas.has(fg.group.id)}
                evidenceOpen={openEvidence.has(fg.group.id)}
                onToggleCollapsed={() => onToggleCollapsed(fg.group.id)}
                onToggleEvidence={() => onToggleEvidence(fg.group.id)}
                excludedHeaders={excludedHeaders}
                onRemove={onRemove}
                onCopy={onCopy}
                busy={busy !== null}
                mutable={mutable}
              />
            ))}
            {visibleGroups.length === 0 ? (
              <p className="rounded-md border border-line bg-app2 px-3 py-2 text-sm text-muted">
                No claims or evidence match the current filter.
              </p>
            ) : null}
          </div>
        </>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {mutable ? (
          <button
            type="button"
            className="rounded-md border border-line2 bg-surface px-3 py-2 text-sm text-ink hover:bg-app2 disabled:opacity-50"
            onClick={onRegenerate}
            disabled={busy !== null}
          >
            {busy === "generate" || busy === "remove" ? "Generating..." : "Regenerate"}
          </button>
        ) : null}
        <button
          type="button"
          className="rounded-md bg-brass px-4 py-2 text-sm font-medium text-onbrass hover:bg-brass disabled:bg-brass-soft disabled:text-faint"
          onClick={onContinue}
          disabled={busy !== null || empty}
          title={empty ? "Generate a package with evidence first" : undefined}
        >
          Continue to safety review
        </button>
      </div>
    </section>
  );
}

/** S48 creator-side coverage contract overview: one row per project stating what
 * the package covers and its boundary, assembled server-side from frozen claims/
 * evidence. Safe metadata only (no exclusion counts here; the creator's exclusion
 * posture stays in the separate ExclusionSummary). Each row is a button that
 * filters the review list below to that project (linking the by-kind counts to the
 * claims they summarize). Renders nothing when empty. */
function CoverageContractSummary({
  entries,
  onSelectProject,
}: {
  entries: CoverageContractEntry[];
  onSelectProject: (label: string) => void;
}) {
  if (entries.length === 0) return null;
  return (
    <section className="mt-4 rounded-md border border-line bg-surface p-3">
      <h3 className="text-sm font-semibold text-ink">Coverage contract</h3>
      <p className="mt-1 text-[11px] text-faint">
        What this package covers per project, and its boundary. Assembled from the cited claims and
        evidence below; the recipient sees the same statements (never your exclusion counts). Select
        a project to filter the review list to its claims.
      </p>
      <ul className="mt-2 space-y-2">
        {entries.map((e) => (
          <li key={e.project_label}>
            <button
              type="button"
              onClick={() => onSelectProject(e.project_label)}
              className="w-full rounded border border-line bg-app2 px-3 py-2 text-left hover:border-brass hover:bg-surface"
              title={`Filter the review list to ${e.project_label}`}
            >
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-sm font-medium text-ink underline decoration-dotted underline-offset-2">
                  {e.project_label}
                </span>
                <span className="text-[11px] text-muted">
                  {e.decisions.length} decided / {e.open_loops.length} open / {e.blockers.length} blocked
                  {e.people.length > 0 ? ` / ${e.people.length} people` : ""}
                </span>
              </div>
              <div className="mt-1 text-xs text-ink">{e.covers_summary}</div>
              <div className="mt-0.5 text-[11px] text-muted">{e.boundary}</div>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Step 4: required S44 safety review. High findings block until pruned or
 * acknowledged with a reason. */
function SafetyStep({
  findings,
  highFindings,
  ackReason,
  onAckReason,
  acknowledged,
  onAcknowledge,
  satisfied,
  onBackToReview,
  onContinue,
}: {
  findings: SafetyFinding[];
  highFindings: SafetyFinding[];
  ackReason: string;
  onAckReason: (v: string) => void;
  acknowledged: boolean;
  onAcknowledge: () => void;
  satisfied: boolean;
  onBackToReview: () => void;
  onContinue: () => void;
}) {
  const hasHigh = highFindings.length > 0;
  return (
    <section className="mt-4 rounded-md border border-line bg-surface p-4">
      <h3 className="text-sm font-semibold text-ink">Safety review</h3>
      <p className="mt-1 text-xs text-muted">
        A required check over this package's own content before publishing. Findings are computed
        by the server and show only safe metadata (category, severity, and a short explanation),
        never the matched text, and they are never shown to the recipient.
      </p>

      {findings.length === 0 ? (
        <div className="mt-3 rounded-md border border-jade bg-jade-soft px-3 py-2 text-sm text-jade">
          No safety findings in this package. You can continue to publish.
        </div>
      ) : (
        <SafetyReviewPanel findings={findings} />
      )}

      {hasHigh ? (
        <div className="mt-3 rounded-md border border-danger-line bg-danger-soft p-3">
          <div className="text-xs font-semibold text-danger">
            {highFindings.length} high-severity finding{highFindings.length === 1 ? "" : "s"} block
            publishing.
          </div>
          <p className="mt-1 text-xs text-danger">
            The preferred fix is to go back, remove the flagged claim or evidence, and regenerate -
            the finding then disappears. If the content is genuinely safe to send, you can
            acknowledge every high finding with a reason (recorded in the audit trail; the reason
            text itself is never stored).
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <button
              type="button"
              className="rounded-md border border-danger-line bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-app2"
              onClick={onBackToReview}
            >
              Back to review and prune
            </button>
          </div>
          <label className="mt-3 block text-xs text-ink">
            Reason to publish anyway (recorded in the audit trail)
            <textarea
              rows={2}
              maxLength={500}
              value={ackReason}
              onChange={(e) => onAckReason(e.target.value)}
              className="mt-1 w-full rounded border border-line2 bg-surface px-2 py-1 text-sm text-ink"
              placeholder="Why is it safe to publish these?"
            />
          </label>
          <button
            type="button"
            className="mt-2 rounded-md border border-danger-line bg-surface px-3 py-1.5 text-xs font-medium text-danger hover:bg-danger-soft disabled:opacity-50"
            onClick={onAcknowledge}
            disabled={!ackReason.trim() || acknowledged}
          >
            {acknowledged ? "High findings acknowledged" : "Acknowledge high findings"}
          </button>
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="rounded-md border border-line2 bg-surface px-3 py-2 text-sm text-ink hover:bg-app2"
          onClick={onBackToReview}
        >
          Back to review
        </button>
        <button
          type="button"
          className="rounded-md bg-brass px-4 py-2 text-sm font-medium text-onbrass hover:bg-brass disabled:bg-brass-soft disabled:text-faint"
          onClick={onContinue}
          disabled={!satisfied}
          title={!satisfied ? "Resolve or acknowledge the high-severity findings first" : undefined}
        >
          Continue to publish
        </button>
      </div>
    </section>
  );
}

/** Step 5: consolidated publish summary + one-time link. */
function PublishStep({
  pkg,
  email,
  days,
  onEmail,
  onDays,
  excludedCount,
  highFindings,
  safetySatisfied,
  busy,
  isReturn,
  suggestedRecipient,
  onBack,
  onPublish,
}: {
  pkg: HandoffPackage;
  email: string;
  days: string;
  onEmail: (v: string) => void;
  onDays: (v: string) => void;
  excludedCount: number;
  highFindings: SafetyFinding[];
  safetySatisfied: boolean;
  busy: string | null;
  isReturn: boolean;
  suggestedRecipient?: string;
  onBack: () => void;
  onPublish: () => void;
}) {
  const parsedDays = parseInt(days, 10);
  const expiresDays = Number.isFinite(parsedDays) && parsedDays > 0 ? parsedDays : 30;
  const noEvidence = pkg.evidence.length === 0;
  const safetyStatus =
    highFindings.length === 0
      ? "No high-severity findings"
      : `${highFindings.length} high-severity finding${highFindings.length === 1 ? "" : "s"} acknowledged`;
  // A return handoff may be published with a BLANK recipient: the server defaults
  // it to the original creator. We do NOT auto-fill the field (so the default path
  // stays exercised) and only allow blank when a suggested recipient is known.
  const canDefaultToOriginal = isReturn && !!suggestedRecipient;
  const recipientOk = canDefaultToOriginal || !!email.trim();
  const recipientLabel = email.trim()
    ? email.trim()
    : canDefaultToOriginal
      ? `${suggestedRecipient} (original employee)`
      : "not set";
  const canPublish = busy === null && recipientOk && !noEvidence && safetySatisfied;

  return (
    <section className="mt-4 rounded-md border border-brass bg-brass-soft p-4">
      <h3 className="text-sm font-semibold text-ink">Publish</h3>
      <p className="mt-1 text-xs text-ink">
        Publishing freezes this package and mints a one-time recipient link. After publishing you
        can no longer edit scope, regenerate, or remove evidence.
      </p>

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
        <SummaryCell label="Recipient" value={recipientLabel} />
        <SummaryCell label="Expires" value={`~${expiresDays} day${expiresDays === 1 ? "" : "s"}`} />
        <SummaryCell label="Safety" value={safetyStatus} />
        <SummaryCell label="Claims" value={String(pkg.claims.length)} />
        <SummaryCell label="Evidence" value={String(pkg.evidence.length)} />
        <SummaryCell label="Removed by you" value={String(excludedCount)} />
        <SummaryCell label="Package" value={pkg.package_type === "return_delta" ? "return" : "coverage"} />
        <SummaryCell label="Version" value={`v${pkg.version}`} />
      </dl>

      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="flex flex-1 flex-col text-xs text-ink">
          Recipient email
          <input
            type="email"
            className="mt-1 rounded border border-brass bg-surface px-2 py-1 text-sm text-ink"
            value={email}
            onChange={(e) => onEmail(e.target.value)}
            placeholder={canDefaultToOriginal ? "Leave blank to send back to the original employee" : "cover@company.com"}
          />
          {canDefaultToOriginal ? (
            <span className="mt-1 text-[11px] text-muted">
              Leave blank to send back to {suggestedRecipient}.
            </span>
          ) : null}
        </label>
        <label className="flex w-28 flex-col text-xs text-ink">
          Expires (days)
          <input
            type="number"
            min={1}
            max={365}
            className="mt-1 rounded border border-brass bg-surface px-2 py-1 text-sm text-ink"
            value={days}
            onChange={(e) => onDays(e.target.value)}
          />
        </label>
      </div>
      <p className="mt-2 text-[11px] text-ink">
        Access expires about {expiresDays} day{expiresDays === 1 ? "" : "s"} after publishing. The
        one-time recipient link is shown exactly once, right after you publish, and cannot be
        recovered later - not even by you.
      </p>
      {noEvidence ? (
        <p className="mt-2 text-xs text-brass">
          This package has no evidence yet. Go back and widen the scope before publishing.
        </p>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="rounded-md border border-line2 bg-surface px-3 py-2 text-sm text-ink hover:bg-app2"
          onClick={onBack}
        >
          Back to safety
        </button>
        <button
          type="button"
          className="rounded-md bg-brass px-4 py-2 text-sm font-medium text-onbrass hover:bg-brass disabled:bg-brass-soft disabled:text-faint"
          onClick={onPublish}
          disabled={!canPublish}
          title={
            !safetySatisfied
              ? "Resolve or acknowledge the high-severity findings first"
              : !recipientOk
                ? "Enter a recipient email first"
                : undefined
          }
        >
          {busy === "publish" ? "Publishing..." : "Publish package"}
        </button>
      </div>
    </section>
  );
}

function SummaryCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-brass bg-surface px-2 py-1">
      <dt className="text-[10px] uppercase tracking-wide text-faint">{label}</dt>
      <dd className="text-ink">{value}</dd>
    </div>
  );
}

/** Terminal step: frozen package summary + one-time link (transient) + post-publish
 * actions. Mirrors the published/revoked states of the detailed surface. */
function DoneStep({
  pkg,
  share,
  busy,
  onRevoke,
  onNewVersion,
  onStartOver,
}: {
  pkg: HandoffPackage;
  share: PublishResponse | null;
  busy: string | null;
  onRevoke: () => void;
  onNewVersion: () => void;
  onStartOver: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const published = pkg.status === "published";
  const shareUrl = share ? `${window.location.origin}/handoff/recipient${share.share_fragment}` : null;
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

  if (!published) {
    const revoked = pkg.status === "revoked";
    return (
      <section className="mt-4 rounded-md border border-line2 bg-app2 p-4">
        <h3 className="text-sm font-semibold text-ink">
          {revoked ? "Access revoked" : "Replaced by a newer version"}
        </h3>
        <p className="mt-1 text-sm text-muted">
          {revoked
            ? `This handoff was revoked${pkg.revoked_at ? ` on ${fmtDate(pkg.revoked_at)}` : ""}. The recipient can no longer open it.`
            : "A newer version of this handoff has been published. This version's access is blocked."}{" "}
          To share again, create a revised version. It starts a fresh draft with this package's
          scope; publishing it mints a new one-time link.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <ReviseButton busy={busy} onClick={onNewVersion} />
          <ExportButton pkg={pkg} />
          <button
            type="button"
            className="rounded-md border border-line2 bg-surface px-3 py-1.5 text-xs font-medium text-muted hover:bg-app2 hover:text-ink"
            onClick={onStartOver}
          >
            Start over
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="mt-4 rounded-md border border-jade bg-jade-soft p-4">
      <h3 className="text-sm font-semibold text-jade">Package published</h3>
      <div className="mt-1 text-xs text-jade">
        {share ? <>Recipient: {share.recipient_email} - </> : null}
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
            <strong>This link is shown once.</strong> Copy it now and store it securely. The code
            cannot be recovered later, not even by you. It is one-time: it is consumed the first time
            it is opened, so do not open it yourself before sending it to the recipient.
          </div>
          <button
            type="button"
            className="mt-2 rounded-md bg-jade px-3 py-1.5 text-xs font-medium text-white hover:bg-jade"
            onClick={copy}
          >
            {copied ? "Copied!" : "Copy link"}
          </button>
        </div>
      ) : (
        <p className="mt-3 text-xs text-jade">
          The share link was shown once, when this package was published, and cannot be recovered
          from the server. If you need the link again, create a revised version below.
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
          {busy === "revoke" ? "Revoking..." : "Revoke access"}
        </button>
        <button
          type="button"
          className="ml-auto rounded-md border border-line2 bg-surface px-3 py-1.5 text-xs font-medium text-muted hover:bg-app2 hover:text-ink"
          onClick={onStartOver}
        >
          Start over
        </button>
      </div>
    </section>
  );
}

function ReviseButton({ busy, onClick }: { busy: string | null; onClick: () => void }) {
  return (
    <button
      type="button"
      className="rounded-md border border-line2 bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-app2 disabled:opacity-50"
      onClick={onClick}
      disabled={busy !== null}
    >
      {busy === "version" ? "Creating..." : "Create revised version"}
    </button>
  );
}

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
