import { useState } from "react";
import { errorKindTitle } from "../api/client";
import { AdminConsole } from "../components/admin/AdminConsole";
import { ContactPanel } from "../components/ContactPanel";
import { CoverForMe } from "../components/CoverForMe";
import { DemoReadinessStrip } from "../components/DemoReadinessStrip";
import { ErrorBanner } from "../components/ErrorBanner";
import { HandoffReview } from "../components/HandoffReview";
import { LoadingSpinner } from "../components/LoadingSpinner";
import { NetworkMap } from "../components/NetworkMap";
import { Overview } from "../components/Overview";
import { ProjectDetail } from "../components/ProjectDetail";
import { RelationshipMap } from "../components/RelationshipMap";
import { ProjectList } from "../components/ProjectList";
import { RoleLegend } from "../components/RoleLegend";
import { StatusScreen } from "../components/StatusScreen";
import { ThemeToggle } from "../components/ThemeToggle";
import { useContactDetail } from "../hooks/useContactDetail";
import { useDemoReadiness } from "../hooks/useDemoReadiness";
import { useNetworkMap } from "../hooks/useNetworkMap";
import { useProjectDetail } from "../hooks/useProjectDetail";
import { useProjects } from "../hooks/useProjects";
import { Link, navigate, usePathname } from "../router";
import { ROLE_COLORS } from "../utils/roleColors";
import { buildSuggestions } from "../utils/suggestions";
import {
  computeReadinessItems,
  INDICATOR_DOT_CLASS,
  overallIndicator,
} from "../utils/readiness";

type Screen =
  | "overview"
  | "network"
  | "relationships"
  | "projects"
  | "cover"
  | "handoff"
  | "status"
  | "admin";

const NAV: { screen: Screen; label: string; path: string }[] = [
  { screen: "overview", label: "Overview", path: "/app" },
  { screen: "network", label: "Network", path: "/app/network" },
  { screen: "relationships", label: "Relationship Map", path: "/app/relationships" },
  { screen: "projects", label: "Projects", path: "/app/projects" },
  { screen: "cover", label: "Cover for Me", path: "/app/cover" },
  { screen: "handoff", label: "Handoff", path: "/app/handoff" },
  { screen: "status", label: "Status", path: "/app/status" },
];

const ENV_MAILBOX_ID = import.meta.env.VITE_MAILBOX_ID ?? "";

// S22 auth boundary: raw mailbox-id entry is a DEV-ONLY affordance. In a
// production build (import.meta.env.DEV === false) it is hidden — the product
// access model is authenticated sign-in (not yet built), never a typed mailbox
// id. This mirrors the backend AUTH_MODE fail-closed behavior.
const AUTH_DEV: boolean = import.meta.env.DEV;

// Persist ONLY the workspace mailbox UUID so creator deep links
// (/app/handoff/<id>) survive a refresh in the same browser session. Deliberately
// sessionStorage (not localStorage) so it does not become a long-lived
// cross-session auth substitute; and deliberately ONLY the mailbox id — never an
// API key, recipient token, capability code, email content, or evidence. This is
// separate from RecipientPackage's own session storage (untouched).
const MAILBOX_STORAGE_KEY = "ekc_workspace_mailbox_id";

function readStoredMailboxId(): string {
  try {
    return window.sessionStorage.getItem(MAILBOX_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

function writeStoredMailboxId(id: string): void {
  try {
    if (id) window.sessionStorage.setItem(MAILBOX_STORAGE_KEY, id);
    else window.sessionStorage.removeItem(MAILBOX_STORAGE_KEY);
  } catch {
    // storage unavailable (private mode / quota) — refresh just won't persist
  }
}

// VITE_MAILBOX_ID wins; else resume the last-loaded mailbox from this session.
const INITIAL_MAILBOX_ID = ENV_MAILBOX_ID || readStoredMailboxId();

function screenForPath(pathname: string): Screen {
  if (pathname === "/app/network") return "network";
  if (pathname === "/app/relationships") return "relationships";
  if (pathname.startsWith("/app/projects")) return "projects";
  if (pathname === "/app/cover") return "cover";
  if (pathname === "/app/handoff" || pathname.startsWith("/app/handoff/")) return "handoff";
  if (pathname === "/app/status") return "status";
  if (pathname === "/app/admin") return "admin";
  return "overview"; // /app, /app/, or any unknown /app/* path
}

/**
 * The workspace shell (S12): persistent app chrome (brand, nav, mailbox, health)
 * wrapping the five workspace screens. Mailbox state and all data hooks live here
 * so they survive navigation between screens; only the inner content panel swaps.
 */
export function Workspace() {
  const pathname = usePathname();
  const screen = screenForPath(pathname);

  // Mailbox id comes from VITE_MAILBOX_ID, else a sessionStorage-resumed value,
  // else the user enters it — so a creator deep-link refresh keeps the mailbox.
  const [mailboxId, setMailboxId] = useState<string>(INITIAL_MAILBOX_ID);
  const [mailboxInput, setMailboxInput] = useState<string>(INITIAL_MAILBOX_ID);

  const [selectedPersonId, setSelectedPersonId] = useState<string | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [activeRoles, setActiveRoles] = useState<Set<string>>(
    new Set(Object.keys(ROLE_COLORS)),
  );
  // Seeded Cover-for-me question from an Overview suggestion (token re-fires ask).
  const [coverSeed, setCoverSeed] = useState<{ query: string; token: number } | null>(
    null,
  );

  const { data, loading, error, errorKind, reload } = useNetworkMap(
    mailboxId || null,
  );
  const {
    detail,
    loading: detailLoading,
    error: detailError,
  } = useContactDetail(mailboxId || null, selectedPersonId);
  const {
    projects,
    loading: projectsLoading,
    error: projectsError,
    errorKind: projectsErrorKind,
    reload: reloadProjects,
  } = useProjects(mailboxId || null);
  const {
    detail: projectDetail,
    loading: projectDetailLoading,
    error: projectDetailError,
  } = useProjectDetail(mailboxId || null, selectedProjectId);

  // One preflight fetch shared by the header health dot, Overview, and Status.
  const {
    checks,
    loading: readinessLoading,
    failed: readinessFailed,
    reload: reloadReadiness,
  } = useDemoReadiness(mailboxId || null);

  const contactCount = loading ? null : (data?.nodes.length ?? null);
  const projectCount = projectsLoading ? null : projects.length;
  const readinessItems = computeReadinessItems({
    checks,
    failed: readinessFailed,
    contactCount,
    projectCount,
  });
  const overall = overallIndicator(readinessItems);

  const toggleRole = (role: string) => {
    setActiveRoles((prev) => {
      const next = new Set(prev);
      if (next.has(role)) next.delete(role);
      else next.add(role);
      return next;
    });
  };

  const askInCover = (query: string) => {
    setCoverSeed({ query, token: Date.now() });
    navigate("/app/cover");
  };
  const openProject = (projectId: string) => {
    setSelectedProjectId(projectId);
    navigate("/app/projects");
  };

  const hasGraph = data && data.nodes.length > 0;
  const showStripUnderHeader =
    screen === "network" ||
    screen === "relationships" ||
    screen === "projects" ||
    screen === "cover";

  return (
    <div className="flex h-full flex-col bg-app2">
      {/* App shell header: brand + nav, then mailbox + health on the right. */}
      <header className="app-header">
        <div className="app-header-left">
          <Link to="/" className="app-brand" title="Back to landing">
            <span className="app-seal" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2 4 6v6c0 5 3.5 8 8 10 4.5-2 8-5 8-10V6z" />
                <path d="m9 12 2 2 4-4" />
              </svg>
            </span>
            Continuity
          </Link>
          <nav className="app-nav" aria-label="Workspace">
            {NAV.map((n) => (
              <Link
                key={n.screen}
                to={n.path}
                className={`app-nav-link${screen === n.screen ? " is-active" : ""}`}
              >
                {n.label}
              </Link>
            ))}
            {/* Admin/Audit is a tenant governance surface, not mailbox-scoped. It is
                shown only in dev builds — production role-gated access is not yet
                wired (S22), so we do not imply it is finished. */}
            {AUTH_DEV ? (
              <Link
                to="/app/admin"
                className={`app-nav-link${screen === "admin" ? " is-active" : ""}`}
              >
                Admin
              </Link>
            ) : null}
          </nav>
        </div>

        <div className="app-header-right">
          {AUTH_DEV ? (
            <form
              className="mailbox-form"
              onSubmit={(e) => {
                e.preventDefault();
                const next = mailboxInput.trim();
                setSelectedPersonId(null);
                setSelectedProjectId(null);
                setMailboxId(next);
                writeStoredMailboxId(next); // persist (or clear) for refresh-safety
              }}
            >
              <input
                type="text"
                value={mailboxInput}
                onChange={(e) => setMailboxInput(e.target.value)}
                placeholder="Mailbox ID (dev)"
                className="mailbox-input"
                aria-label="Mailbox ID (dev only)"
              />
              <button type="submit" className="mailbox-load">
                Load
              </button>
            </form>
          ) : (
            <span
              className="mailbox-prod-note"
              title="Raw mailbox-id loading is a dev-only affordance. Production access is authenticated sign-in (not yet built)."
            >
              Sign-in required
            </span>
          )}
          <Link
            to="/app/status"
            className="health-dot-link"
            title={`Readiness: ${overall}${
              readinessLoading ? " (checking…)" : ""
            } — open Status`}
          >
            <span className={INDICATOR_DOT_CLASS[overall]} aria-hidden="true" />
            <span className="health-dot-label">Status</span>
          </Link>
          <ThemeToggle />
        </div>
      </header>

      {/* Unobtrusive readiness strip on working screens (not overview/status,
          which already show readiness in detail). */}
      {mailboxId && showStripUnderHeader ? (
        <DemoReadinessStrip
          checks={checks}
          loading={readinessLoading}
          failed={readinessFailed}
          contactCount={contactCount}
          projectCount={projectCount}
        />
      ) : null}

      <main className="relative flex-1 overflow-hidden flex">
        {screen === "admin" ? (
          // Tenant-scoped governance surface — renders without a loaded mailbox.
          <div className="w-full overflow-y-auto bg-app2">
            <AdminConsole />
          </div>
        ) : !mailboxId ? (
          <div className="flex h-full w-full items-center justify-center px-6 text-center text-muted">
            <div>
              {AUTH_DEV ? (
                <>
                  <p className="text-lg font-medium text-ink">
                    Load a mailbox to begin.
                  </p>
                  <p className="mt-1 text-sm text-faint">
                    Enter a mailbox ID above, run{" "}
                    <code>python scripts/dev_seed.py</code> to create a fixture
                    mailbox, or set <code>VITE_MAILBOX_ID</code>.
                  </p>
                </>
              ) : (
                <>
                  <p className="text-lg font-medium text-ink">
                    Sign-in required.
                  </p>
                  <p className="mt-1 text-sm text-faint">
                    A workspace loads for an authenticated mailbox owner.
                    Production sign-in is not built yet (S22); run locally with{" "}
                    <code>AUTH_MODE=dev</code> to use the developer workflow.
                  </p>
                </>
              )}
            </div>
          </div>
        ) : screen === "overview" ? (
          <div className="w-full overflow-y-auto">
            <Overview
              ownerName={data?.owner.name ?? null}
              contactCount={contactCount}
              projectCount={projectCount}
              projects={projects}
              readinessItems={readinessItems}
              onAskSuggested={askInCover}
              onOpenProject={openProject}
            />
          </div>
        ) : screen === "status" ? (
          <div className="w-full overflow-y-auto">
            <StatusScreen
              mailboxId={mailboxId}
              checks={checks}
              loading={readinessLoading}
              failed={readinessFailed}
              onRefresh={reloadReadiness}
            />
          </div>
        ) : screen === "network" ? (
          <div className="flex h-full w-full flex-col">
            {data ? (
              <div className="network-legend-bar">
                <span className="network-owner">
                  {data.owner.name} · {data.nodes.length} contacts
                </span>
                <RoleLegend
                  nodes={data.nodes}
                  activeRoles={activeRoles}
                  onToggle={toggleRole}
                />
              </div>
            ) : null}
            <div className="relative flex-1 overflow-hidden">
              {loading ? (
                <div className="flex h-full items-center justify-center">
                  <LoadingSpinner label="Loading network map…" />
                </div>
              ) : error ? (
                <div className="mx-auto mt-8 max-w-lg px-4">
                  <ErrorBanner
                    title={errorKindTitle(errorKind)}
                    message={error}
                    onRetry={reload}
                  />
                </div>
              ) : !hasGraph ? (
                <div className="flex h-full items-center justify-center px-6 text-center text-muted">
                  <div>
                    <p className="text-lg font-medium text-ink">
                      No contacts yet.
                    </p>
                    <p className="mt-1 text-sm text-faint">
                      The graph is empty — run the ingest pipeline for this
                      mailbox.
                    </p>
                  </div>
                </div>
              ) : (
                <NetworkMap
                  data={data}
                  activeRoles={activeRoles}
                  selectedPersonId={selectedPersonId}
                  onNodeClick={(node) => setSelectedPersonId(node.person_id)}
                />
              )}
            </div>
          </div>
        ) : screen === "projects" ? (
          <>
            {projectsLoading ? (
              <div className="flex h-full w-full items-center justify-center">
                <LoadingSpinner label="Loading projects…" />
              </div>
            ) : projectsError ? (
              <div className="mx-auto mt-8 max-w-lg px-4 w-full">
                <ErrorBanner
                  title={errorKindTitle(projectsErrorKind)}
                  message={projectsError}
                  onRetry={reloadProjects}
                />
              </div>
            ) : (
              <div className="flex w-full overflow-hidden">
                <aside className="w-72 flex-shrink-0 overflow-y-auto border-r border-line bg-surface">
                  <ProjectList
                    projects={projects ?? []}
                    selectedProjectId={selectedProjectId}
                    onSelect={setSelectedProjectId}
                  />
                </aside>
                <div className="flex-1 overflow-y-auto bg-app2">
                  {projectDetailLoading ? (
                    <div className="flex h-full items-center justify-center">
                      <LoadingSpinner label="Loading project…" />
                    </div>
                  ) : projectDetailError ? (
                    <div className="mx-auto mt-8 max-w-lg px-4">
                      <ErrorBanner message={projectDetailError} />
                    </div>
                  ) : selectedProjectId && projectDetail ? (
                    <ProjectDetail detail={projectDetail} mailboxId={mailboxId} />
                  ) : (
                    <div className="flex h-full items-center justify-center text-faint text-sm">
                      {projects && projects.length > 0
                        ? "Select a project to see details."
                        : "No projects yet — run the clustering pipeline."}
                    </div>
                  )}
                </div>
              </div>
            )}
          </>
        ) : screen === "relationships" ? (
          <div className="w-full overflow-hidden">
            <RelationshipMap mailboxId={mailboxId} projects={projects} />
          </div>
        ) : screen === "cover" ? (
          <div className="w-full overflow-y-auto bg-app2">
            <CoverForMe
              mailboxId={mailboxId}
              seed={coverSeed}
              suggestions={buildSuggestions(projects)}
            />
          </div>
        ) : screen === "handoff" ? (
          <div className="w-full overflow-y-auto bg-app2">
            <HandoffReview mailboxId={mailboxId} />
          </div>
        ) : null}
      </main>

      <ContactPanel
        detail={detail}
        loading={detailLoading}
        error={detailError}
        open={screen === "network" && selectedPersonId !== null}
        onClose={() => setSelectedPersonId(null)}
        mailboxId={mailboxId || null}
        personId={selectedPersonId}
      />
    </div>
  );
}
