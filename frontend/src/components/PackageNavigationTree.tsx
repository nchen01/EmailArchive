import type { RecipientClaim, RecipientEvidence } from "../api/types";
import {
  buildPackageTree,
  CLAIMS_ANCHOR,
  EVIDENCE_ANCHOR,
  type TreeLeaf,
} from "../utils/packageTree";

/**
 * Package-local navigation tree (S17.12).
 *
 * A compact, document-style outline of the recipient package — NOT a graph and
 * NOT the live Relationship Map. It is derived purely from the snapshotted
 * `claims` + `evidence` (see `buildPackageTree`), shows no Message-IDs / mailbox
 * id / counts-of-excluded / source links, and only scrolls the reader around the
 * page. Clicking a leaf jumps to the matching claim group or evidence card and
 * briefly flashes it (`.nav-flash`, defined in index.css).
 */

/** Smooth-scroll to a DOM anchor and flash it so the reader sees where they landed. */
function jumpTo(anchorId: string): void {
  const el = document.getElementById(anchorId);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "start" });
  el.classList.remove("nav-flash");
  void el.offsetWidth; // restart the animation if the class is re-added quickly
  el.classList.add("nav-flash");
  window.setTimeout(() => el.classList.remove("nav-flash"), 1300);
}

function TreeLink({ label, count, anchorId }: TreeLeaf) {
  return (
    <li>
      <button
        type="button"
        onClick={() => jumpTo(anchorId)}
        className="flex w-full items-baseline justify-between gap-2 rounded px-2 py-1 text-left text-sm text-slate-600 hover:bg-white hover:text-indigo-700"
      >
        <span className="truncate">{label}</span>
        <span className="shrink-0 text-xs text-slate-400">{count}</span>
      </button>
    </li>
  );
}

function TreeGroup({
  heading,
  anchorId,
  leaves,
}: {
  heading: string;
  anchorId: string;
  leaves: TreeLeaf[];
}) {
  return (
    <div>
      <button
        type="button"
        onClick={() => jumpTo(anchorId)}
        className="rounded px-2 py-1 text-xs font-semibold uppercase tracking-wide text-indigo-500 hover:text-indigo-700"
      >
        {heading}
      </button>
      {leaves.length > 0 ? (
        <ul className="ml-2 mt-0.5 space-y-0.5 border-l border-slate-200 pl-2">
          {leaves.map((leaf) => (
            <TreeLink key={leaf.id} {...leaf} />
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function PackageNavigationTree({
  claims,
  evidence,
}: {
  claims: RecipientClaim[];
  evidence: RecipientEvidence[];
}) {
  const tree = buildPackageTree(claims, evidence);
  // Nothing to navigate → render nothing (the document shows its own empty
  // states). Keeps the view quiet rather than showing an empty outline.
  if (tree.claimCount === 0 && tree.evidenceCount === 0) return null;

  return (
    <nav
      aria-label="Handoff contents"
      className="mt-4 rounded-md border border-slate-200 bg-slate-50 px-3 py-3"
    >
      <div className="px-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
        In this handoff
      </div>
      <div className="mt-2 space-y-2">
        {tree.claimGroups.length > 0 ? (
          <TreeGroup
            heading="What you need to know"
            anchorId={CLAIMS_ANCHOR}
            leaves={tree.claimGroups}
          />
        ) : null}
        {tree.domainGroups.length > 0 ? (
          <TreeGroup
            heading="People and domains"
            anchorId={EVIDENCE_ANCHOR}
            leaves={tree.domainGroups}
          />
        ) : null}
        <button
          type="button"
          onClick={() => jumpTo(EVIDENCE_ANCHOR)}
          className="rounded px-2 py-1 text-xs font-semibold uppercase tracking-wide text-indigo-500 hover:text-indigo-700"
        >
          Supporting messages
          <span className="ml-1 text-slate-400">{tree.evidenceCount}</span>
        </button>
      </div>
    </nav>
  );
}
