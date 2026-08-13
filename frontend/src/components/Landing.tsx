import { useEffect, useRef } from "react";
import { Link } from "../router";
import { ThemeToggle } from "./ThemeToggle";

/**
 * Marketing landing page — "Custody Ledger" visual system (S17 redesign).
 *
 * A precision-dossier identity for an audited handoff product: ink hero band,
 * editorial serif display, a technical mono for record/audit labels, and one
 * burnished-brass accent used like an official seal. Routes into the workspace.
 *
 * The lifecycle is genuinely numbered because the handoff IS a sequence
 * (scope → generate → review → publish → hand off → revoke).
 */

/** The brass "seal" mark — a shield used for the brand and scope-verified posture. */
function Shield({ check = false }: { check?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 2 4 6v6c0 5 3.5 8 8 10 4.5-2 8-5 8-10V6z" />
      {check ? <path d="m9 12 2 2 4-4" /> : null}
    </svg>
  );
}

export function Landing() {
  // Reset scroll to the top on every entry (direct load or the in-app brand
  // link) so the landing presents consistently — its own scroll container can
  // otherwise carry a residual offset after client navigation.
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    rootRef.current?.scrollTo({ top: 0, left: 0 });
    window.scrollTo(0, 0);
    if (!("IntersectionObserver" in window)) return;
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            e.target.classList.add("in");
            io.unobserve(e.target);
          }
        }
      },
      { threshold: 0.12 },
    );
    rootRef.current?.querySelectorAll(".lx-reveal").forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);

  return (
    <div className="landing" ref={rootRef}>
      {/* Topbar */}
      <header className="lx-top">
        <div className="lx-wrap lx-top-in">
          <a className="lx-brand" href="#top">
            <span className="lx-seal"><Shield /></span>
            Continuity
          </a>
          <nav className="lx-nav">
            <a className="lx-navlink lx-nav-desktop" href="#how">How it works</a>
            <a className="lx-navlink lx-nav-desktop" href="#custody">Chain of custody</a>
            <Link to="/app/status" className="lx-navlink lx-nav-desktop">Setup</Link>
            <ThemeToggle className="lx-theme-toggle" />
            <Link to="/app" className="lx-btn lx-btn-primary">Open workspace</Link>
          </nav>
        </div>
      </header>

      {/* Hero */}
      <section className="lx-hero" id="top">
        <div className="lx-wrap lx-hero-grid">
          <div>
            <p className="lx-eyebrow">Audited handoff packages</p>
            <h1 className="lx-h1">When someone steps away,<br />the work doesn't <em>go dark</em>.</h1>
            <p className="lx-lede">
              Continuity turns a covered employee's email into a scoped, cited, revocable handoff
              package, so whoever covers them opens with the decisions, open loops, and the evidence
              behind each one. Nothing sensitive. Nothing off-scope.
            </p>
            <div className="lx-cta-row">
              <Link to="/app" className="lx-btn lx-btn-primary">Open the workspace</Link>
              <a href="#how" className="lx-btn lx-btn-ghost">See how it works</a>
            </div>
            <div className="lx-meta">
              <div><span className="n">One-time</span><span className="l">Recipient link</span></div>
              <div><span className="n">Every claim</span><span className="l">Cited to evidence</span></div>
              <div><span className="n">Revocable</span><span className="l">On your say-so</span></div>
            </div>
          </div>

          {/* sealed package preview */}
          <div className="lx-card" aria-hidden="true">
            <div className="lx-card-band">
              <div>
                <div className="t">Covering Dana: Nexus Auth &amp; on-call</div>
                <div className="s">HANDOFF · v2 · LINEAGE 7f3a…c1</div>
              </div>
              <span className="lx-stamp">SEALED</span>
            </div>
            <div className="lx-card-body">
              <div className="lx-post"><Shield /> Scope-limited · sensitive &amp; out-of-scope excluded</div>
              <div className="lx-clbl">Decisions &amp; outcomes</div>
              <div className="lx-claim">Shipped the Nexus Auth SSO cutover to production.
                <span className="lx-cite">nexus-1 · dana@acme.dev</span></div>
              <div className="lx-claim">Renewed the Contoso support contract for 12 months.
                <span className="lx-cite">pool-2 · datadoghq.com</span></div>
              <div className="lx-clbl">Open loops</div>
              <div className="lx-claim">Migrate remaining internal apps to Nexus Auth next sprint.
                <span className="lx-cite">nexus-2 · dana@acme.dev</span></div>
            </div>
          </div>
        </div>
      </section>

      {/* Principle */}
      <section className="lx-section" id="how">
        <div className="lx-wrap">
          <div className="lx-sechead">
            <div>
              <p className="lx-eyebrow">The principle</p>
              <h2>A coverage brief you can trust, because it can't overreach.</h2>
            </div>
            <p>Not a chatbot loose in an inbox. A deliberate, package-local artifact. Every fact
              carries its citation, and the recipient never touches the live mailbox.</p>
          </div>
          <div className="lx-ledger lx-reveal">
            <div className="lx-lrow">
              <div className="ix">01</div>
              <h3>Scoped</h3>
              <div><p>The covered employee chooses what's in (by date, project, and person), then
                prunes anything that shouldn't travel. Whole-thread sensitivity and noise are excluded
                before a single word is snapshotted.</p>
                <span className="lx-tag">Sensitive &amp; noise never enter</span></div>
            </div>
            <div className="lx-lrow">
              <div className="ix">02</div>
              <h3>Cited</h3>
              <div><p>Claims are drawn only from already-extracted, already-cited events. No citation,
                no claim. Ask a question and the answer is grounded in this package's evidence, never
                the mailbox behind it.</p>
                <span className="lx-tag">Every claim → its evidence</span></div>
            </div>
            <div className="lx-lrow">
              <div className="ix">03</div>
              <h3>Revocable</h3>
              <div><p>Publish mints a one-time link to a single recipient with a 30-day grant. Revoke,
                or supersede with a new version, and access ends at once, with every step written to
                an append-only audit trail.</p>
                <span className="lx-tag">One-time code · full audit</span></div>
            </div>
          </div>
        </div>
      </section>

      {/* Chain of custody */}
      <section className="lx-section alt" id="custody">
        <div className="lx-wrap">
          <div className="lx-sechead">
            <div>
              <p className="lx-eyebrow">Chain of custody</p>
              <h2>Six steps, from scope to seal.</h2>
            </div>
            <p>The handoff is a sequence, and Continuity treats it like one. Each stage leaves a record.</p>
          </div>
          <div className="lx-ledger lx-reveal">
            <div className="lx-lrow"><div className="ix">01</div><h3>Scope</h3><div><p>Pick the date window, projects, and people. Exclude threads by hand.</p></div></div>
            <div className="lx-lrow"><div className="ix">02</div><h3>Generate</h3><div><p>Claims + snapshotted evidence assemble; sensitive and noise drop out.</p></div></div>
            <div className="lx-lrow"><div className="ix">03</div><h3>Review</h3><div><p>Read the candidate, remove any evidence that shouldn't leave.</p></div></div>
            <div className="lx-lrow"><div className="ix">04</div><h3>Publish</h3><div><p>One recipient, one-time link, 30-day access. The code is shown once.</p></div></div>
            <div className="lx-lrow"><div className="ix">05</div><h3>Hand off</h3><div><p>They read a package-local brief and can ask questions of it.</p></div></div>
            <div className="lx-lrow"><div className="ix">06</div><h3>Revoke</h3><div><p>End access, or supersede with v2. Old links go dark instantly.</p></div></div>
          </div>
        </div>
      </section>

      {/* Close */}
      <section className="lx-section" style={{ borderBottom: "none" }}>
        <div className="lx-wrap">
          <div className="lx-sechead">
            <div>
              <p className="lx-eyebrow">Open the workspace</p>
              <h2>Scope a handoff, or read one you've been handed.</h2>
            </div>
            <p>Start in the workspace to build and publish a package, or check setup if you're wiring
              up a fresh environment.</p>
          </div>
          <div className="lx-cta-row" style={{ marginTop: "26px" }}>
            <Link to="/app" className="lx-btn lx-btn-primary">Open the workspace</Link>
            <Link to="/app/status" className="lx-btn lx-btn-ghost">Check setup</Link>
          </div>
        </div>
      </section>

      <footer className="lx-foot">
        <div className="lx-wrap lx-foot-in">
          <a className="lx-brand" href="#top"><span className="lx-seal"><Shield /></span>Continuity</a>
          <p>Audited handoff packages · Email Knowledge Continuity</p>
        </div>
      </footer>
    </div>
  );
}
