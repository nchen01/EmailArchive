import { useState } from "react";
import { errorKindTitle } from "../api/client";
import { ContactPanel } from "../components/ContactPanel";
import { CoverForMe } from "../components/CoverForMe";
import { DemoReadinessStrip } from "../components/DemoReadinessStrip";
import { ErrorBanner } from "../components/ErrorBanner";
import { LoadingSpinner } from "../components/LoadingSpinner";
import { NetworkMap } from "../components/NetworkMap";
import { Overview } from "../components/Overview";
import { ProjectDetail } from "../components/ProjectDetail";
import { RelationshipMap } from "../components/RelationshipMap";
import { ProjectList } from "../components/ProjectList";
import { RoleLegend } from "../components/RoleLegend";
import { StatusScreen } from "../components/StatusScreen";
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
  | "status";

const NAV: { screen: Screen; label: string; path: string }[] = [
  { screen: "overview", label: "Overview", path: "/app" },
  { screen: "network", label: "Network", path: "/app/network" },
  { screen: "relationships", label: "Relationship Map", path: "/app/relationships" },
  { screen: "projects", label: "Projects", path: "/app/projects" },
  { screen: "cover", label: "Cover for Me", path: "/app/cover" },
  { screen: "status", label: "Status", path: "/app/status" },
];

const ENV_MAILBOX_ID = import.meta.env.VITE_MAILBOX_ID ?? "";

function screenForPath(pathname: string): Screen {
  if (pathname === "/app/network") return "network";
  if (pathname === "/app/relationships") return "relationships";
  if (pathname.startsWith("/app/projects")) return "projects";
  if (pathname === "/app/cover") return "cover";
  if (pathname === "/app/status") return "status";
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

  // Mailbox id comes from VITE_MAILBOX_ID, otherwise the user enters it.
  const [mailboxId, setMailboxId] = useState<string>(ENV_MAILBOX_ID);
  const [mailboxInput, setMailboxInput] = useState<string>(ENV_MAILBOX_ID);

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
    <div className="flex h-full flex-col bg-slate-50">
      {/* App shell header: brand + nav, then mailbox + health on the right. */}
      <header className="app-header">
        <div className="app-header-left">
          <Link to="/" className="app-brand" title="Back to landing">
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
          </nav>
        </div>

        <div className="app-header-right">
          <form
            className="mailbox-form"
            onSubmit={(e) => {
              e.preventDefault();
              setSelectedPersonId(null);
              setSelectedProjectId(null);
              setMailboxId(mailboxInput.trim());
            }}
          >
            <input
              type="text"
              value={mailboxInput}
              onChange={(e) => setMailboxInput(e.target.value)}
              placeholder="Mailbox ID"
              className="mailbox-input"
              aria-label="Mailbox ID"
            />
            <button type="submit" className="mailbox-load">
              Load
            </button>
          </form>
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
        {!mailboxId ? (
          <div className="flex h-full w-full items-center justify-center px-6 text-center text-slate-500">
            <div>
              <p className="text-lg font-medium text-slate-700">
                Load a mailbox to begin.
              </p>
              <p className="mt-1 text-sm text-slate-400">
                Enter a mailbox ID above, run{" "}
                <code>python scripts/dev_seed.py</code> to create a fixture
                mailbox, or set <code>VITE_MAILBOX_ID</code>.
              </p>
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
                <div className="flex h-full items-center justify-center px-6 text-center text-slate-500">
                  <div>
                    <p className="text-lg font-medium text-slate-700">
                      No contacts yet.
                    </p>
                    <p className="mt-1 text-sm text-slate-400">
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
                <aside className="w-72 flex-shrink-0 overflow-y-auto border-r border-slate-200 bg-white">
                  <ProjectList
                    projects={projects ?? []}
                    selectedProjectId={selectedProjectId}
                    onSelect={setSelectedProjectId}
                  />
                </aside>
                <div className="flex-1 overflow-y-auto bg-slate-50">
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
                    <div className="flex h-full items-center justify-center text-slate-400 text-sm">
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
          <div className="w-full overflow-y-auto bg-slate-50">
            <CoverForMe
              mailboxId={mailboxId}
              seed={coverSeed}
              suggestions={buildSuggestions(projects)}
            />
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
