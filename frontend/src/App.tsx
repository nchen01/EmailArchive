import { useState } from "react";
import { ContactPanel } from "./components/ContactPanel";
import { ErrorBanner } from "./components/ErrorBanner";
import { LoadingSpinner } from "./components/LoadingSpinner";
import { NetworkMap } from "./components/NetworkMap";
import { ProjectDetail } from "./components/ProjectDetail";
import { ProjectList } from "./components/ProjectList";
import { RoleLegend } from "./components/RoleLegend";
import { useContactDetail } from "./hooks/useContactDetail";
import { useNetworkMap } from "./hooks/useNetworkMap";
import { useProjectDetail } from "./hooks/useProjectDetail";
import { useProjects } from "./hooks/useProjects";
import { ROLE_COLORS } from "./utils/roleColors";

type ViewTab = "network" | "projects";

const ENV_MAILBOX_ID = import.meta.env.VITE_MAILBOX_ID ?? "";

export default function App() {
  // Mailbox id comes from VITE_MAILBOX_ID, otherwise the user enters it.
  const [mailboxId, setMailboxId] = useState<string>(ENV_MAILBOX_ID);
  const [mailboxInput, setMailboxInput] = useState<string>(ENV_MAILBOX_ID);

  const [activeTab, setActiveTab] = useState<ViewTab>("network");
  const [selectedPersonId, setSelectedPersonId] = useState<string | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [activeRoles, setActiveRoles] = useState<Set<string>>(
    new Set(Object.keys(ROLE_COLORS)),
  );

  const { data, loading, error, reload } = useNetworkMap(mailboxId || null);
  const {
    detail,
    loading: detailLoading,
    error: detailError,
  } = useContactDetail(mailboxId || null, selectedPersonId);

  const { projects, loading: projectsLoading, error: projectsError } =
    useProjects(mailboxId || null);
  const {
    detail: projectDetail,
    loading: projectDetailLoading,
    error: projectDetailError,
  } = useProjectDetail(mailboxId || null, selectedProjectId);

  const toggleRole = (role: string) => {
    setActiveRoles((prev) => {
      const next = new Set(prev);
      if (next.has(role)) next.delete(role);
      else next.add(role);
      return next;
    });
  };

  const hasGraph = data && data.nodes.length > 0;

  return (
    <div className="flex h-full flex-col bg-slate-50">
      {/* Top bar: title + mailbox input + legend */}
      <header className="z-10 border-b border-slate-200 bg-white px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex rounded-md border border-slate-200 overflow-hidden">
              {(["network", "projects"] as ViewTab[]).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  onClick={() => setActiveTab(tab)}
                  className={`px-3 py-1 text-sm font-medium capitalize transition-colors ${
                    activeTab === tab
                      ? "bg-slate-800 text-white"
                      : "bg-white text-slate-600 hover:bg-slate-50"
                  }`}
                >
                  {tab === "network" ? "Network Map" : "Projects"}
                </button>
              ))}
            </div>
            {activeTab === "network" && data ? (
              <span className="text-sm text-slate-400">
                {data.owner.name} · {data.nodes.length} contacts
              </span>
            ) : null}
            {activeTab === "projects" && projects ? (
              <span className="text-sm text-slate-400">
                {projects.length} project{projects.length === 1 ? "" : "s"}
              </span>
            ) : null}
          </div>

          <form
            className="flex items-center gap-2"
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
              placeholder="Mailbox ID (UUID)"
              className="w-72 rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-slate-400 focus:outline-none"
            />
            <button
              type="submit"
              className="rounded-md bg-slate-800 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
            >
              Load
            </button>
          </form>
        </div>

        {activeTab === "network" && data ? (
          <div className="mt-3">
            <RoleLegend
              nodes={data.nodes}
              activeRoles={activeRoles}
              onToggle={toggleRole}
            />
          </div>
        ) : null}
      </header>

      {/* Main content area */}
      <main className="relative flex-1 overflow-hidden flex">
        {!mailboxId ? (
          <div className="flex h-full items-center justify-center px-6 text-center text-slate-500">
            <div>
              <p className="text-lg font-medium text-slate-700">
                Enter a mailbox ID to load the network map.
              </p>
              <p className="mt-1 text-sm text-slate-400">
                Run <code>python scripts/dev_seed.py</code> to create a fixture
                mailbox, or set <code>VITE_MAILBOX_ID</code>.
              </p>
            </div>
          </div>
        ) : null}

        {activeTab === "network" && mailboxId && loading ? (
          <div className="flex h-full items-center justify-center">
            <LoadingSpinner label="Loading network map…" />
          </div>
        ) : null}

        {activeTab === "network" && mailboxId && error && !loading ? (
          <div className="mx-auto mt-8 max-w-lg px-4">
            <ErrorBanner message={error} onRetry={reload} />
          </div>
        ) : null}

        {activeTab === "network" && mailboxId && !loading && !error && !hasGraph ? (
          <div className="flex h-full items-center justify-center px-6 text-center text-slate-500">
            <div>
              <p className="text-lg font-medium text-slate-700">
                No contacts yet.
              </p>
              <p className="mt-1 text-sm text-slate-400">
                The graph is empty — run the ingest pipeline for this mailbox.
              </p>
            </div>
          </div>
        ) : null}

        {activeTab === "network" && mailboxId && !loading && !error && hasGraph ? (
          <NetworkMap
            data={data}
            activeRoles={activeRoles}
            selectedPersonId={selectedPersonId}
            onNodeClick={(node) => setSelectedPersonId(node.person_id)}
          />
        ) : null}

        {/* Projects view */}
        {activeTab === "projects" && mailboxId ? (
          <>
            {projectsLoading && (
              <div className="flex h-full w-full items-center justify-center">
                <LoadingSpinner label="Loading projects…" />
              </div>
            )}
            {projectsError && !projectsLoading && (
              <div className="mx-auto mt-8 max-w-lg px-4 w-full">
                <ErrorBanner message={projectsError} />
              </div>
            )}
            {!projectsLoading && !projectsError && (
              <div className="flex w-full overflow-hidden">
                <aside className="w-72 flex-shrink-0 overflow-y-auto border-r border-slate-200 bg-white">
                  <ProjectList
                    projects={projects ?? []}
                    selectedProjectId={selectedProjectId}
                    onSelect={setSelectedProjectId}
                  />
                </aside>
                <div className="flex-1 overflow-y-auto bg-slate-50">
                  {projectDetailLoading && (
                    <div className="flex h-full items-center justify-center">
                      <LoadingSpinner label="Loading project…" />
                    </div>
                  )}
                  {projectDetailError && !projectDetailLoading && (
                    <div className="mx-auto mt-8 max-w-lg px-4">
                      <ErrorBanner message={projectDetailError} />
                    </div>
                  )}
                  {selectedProjectId && projectDetail && !projectDetailLoading ? (
                    <ProjectDetail
                      detail={projectDetail}
                      mailboxId={mailboxId}
                    />
                  ) : !projectDetailLoading && !projectDetailError ? (
                    <div className="flex h-full items-center justify-center text-slate-400 text-sm">
                      {projects && projects.length > 0
                        ? "Select a project to see details."
                        : "No projects yet — run the clustering pipeline."}
                    </div>
                  ) : null}
                </div>
              </div>
            )}
          </>
        ) : null}
      </main>

      <ContactPanel
        detail={detail}
        loading={detailLoading}
        error={detailError}
        open={activeTab === "network" && selectedPersonId !== null}
        onClose={() => setSelectedPersonId(null)}
        mailboxId={mailboxId || null}
        personId={selectedPersonId}
      />
    </div>
  );
}
