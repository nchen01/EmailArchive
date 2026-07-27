import { useEffect, useRef } from "react";
import { Link } from "../router";

/**
 * Marketing landing page (S12.4). Professional, calm, operational tone — framed
 * around continuity / evidence / coverage, not "AI email assistant". Routes into
 * the real workspace: primary CTA -> /app, secondary -> /app/status.
 *
 * The hero "preview" is a lightweight CSS mock of the actual workspace (not a
 * binary screenshot), so it stays in sync with the product and adds no asset
 * weight.
 */
export function Landing() {
  // The landing has its own internal scroll container. On a fresh page load it
  // opens at the top, but arriving via the in-app "Continuity" brand link
  // (client navigation from /app) can leave a residual scroll offset, so the page
  // appeared shifted down vs. a direct load. Reset to the top on mount so the top
  // of the landing is the default for every entry point.
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    rootRef.current?.scrollTo({ top: 0, left: 0 });
    window.scrollTo(0, 0);
  }, []);

  return (
    <div className="landing" ref={rootRef}>
      {/* Top bar */}
      <header className="landing-topbar">
        <span className="landing-brand">Continuity</span>
        <nav className="landing-topnav">
          <a href="#how">How it works</a>
          <a href="#trust">Trust</a>
          <Link to="/app/status">Setup</Link>
          <Link to="/app" className="landing-topnav-cta">
            Open workspace
          </Link>
        </nav>
      </header>

      {/* Hero */}
      <section className="landing-hero">
        <div className="landing-hero-copy">
          <p className="landing-eyebrow">Email continuity workspace</p>
          <h1>
            Turn an authorized mailbox into a cited map of people, projects, and
            work history.
          </h1>
          <p className="landing-lede">
            So a teammate can get oriented and cover work without guessing. Not a
            chatbot reading an inbox — a continuity workspace built from
            structured email evidence, where every answer is cited.
          </p>
          <div className="landing-cta-row">
            <Link to="/app" className="landing-cta-primary">
              Open demo workspace
            </Link>
            <Link to="/app/status" className="landing-cta-secondary">
              Check setup
            </Link>
          </div>
          <p className="landing-cta-note">
            Built for coverage, handoffs, and institutional memory.
          </p>
        </div>

        {/* Realistic product preview (CSS mock of the workspace). */}
        <div className="landing-preview" aria-hidden="true">
          <div className="lp-chrome">
            <span className="lp-dot" />
            <span className="lp-dot" />
            <span className="lp-dot" />
            <span className="lp-tab is-active">Overview</span>
            <span className="lp-tab">Network</span>
            <span className="lp-tab">Projects</span>
            <span className="lp-tab">Cover for Me</span>
          </div>
          <div className="lp-body">
            <div className="lp-stats">
              <div className="lp-stat"><span>People</span><strong>34</strong></div>
              <div className="lp-stat"><span>Projects</span><strong>7</strong></div>
              <div className="lp-stat"><span>Retrieval</span><strong className="lp-ok">Ready</strong></div>
            </div>
            <div className="lp-claim">
              <p>The auth layer was reported code-complete and in QA as of Mar 27.</p>
              <div className="lp-cites">
                <span className="lp-chip">Nexus launch readiness · 3/27</span>
                <span className="lp-chip">Board deck Q2 · 5/31</span>
              </div>
            </div>
            <div className="lp-claim">
              <p>Circuit breaker merged; load-testing in progress.</p>
              <div className="lp-cites">
                <span className="lp-chip">Nexus launch readiness · 3/27</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Three core surfaces */}
      <section className="landing-section">
        <h2 className="landing-h2">Three surfaces, one mailbox</h2>
        <div className="landing-cards">
          <article className="landing-card">
            <h3>Network Map</h3>
            <p>
              See who works with whom. Edge weight is communication volume, not a
              claim of impact.
            </p>
          </article>
          <article className="landing-card">
            <h3>Projects</h3>
            <p>
              See what work is active, who is involved, and the cited timeline of
              what has actually happened.
            </p>
          </article>
          <article className="landing-card">
            <h3>Cover for Me</h3>
            <p>
              Ask plain-language questions across the mailbox and get answers
              grounded in cited messages you can open and inspect.
            </p>
          </article>
        </div>
      </section>

      {/* Trust model */}
      <section id="trust" className="landing-section landing-section-alt">
        <h2 className="landing-h2">Built to be trusted</h2>
        <ul className="landing-trust">
          <li><strong>Every answer is cited.</strong> No citation, no claim.</li>
          <li><strong>Inspectable evidence.</strong> Open any citation to see the source message's subject, date, and snippet.</li>
          <li><strong>Sensitive content excluded by default.</strong> HR, legal, and personal threads are gated out of retrieval.</li>
          <li><strong>Retrieval status is visible.</strong> You always know whether an answer used message search or structured data only.</li>
          <li><strong>Secrets stay out.</strong> OAuth tokens and provider keys are never stored in the app database or logs.</li>
        </ul>
      </section>

      {/* How it works */}
      <section id="how" className="landing-section">
        <h2 className="landing-h2">How it works</h2>
        <ol className="landing-steps">
          <li>
            <span className="landing-step-n">1</span>
            <div>
              <h3>Ingest</h3>
              <p>Pull authorized mailbox data; normalize and clean it.</p>
            </div>
          </li>
          <li>
            <span className="landing-step-n">2</span>
            <div>
              <h3>Structure</h3>
              <p>Materialize contacts, threads, relationships, projects, and events.</p>
            </div>
          </li>
          <li>
            <span className="landing-step-n">3</span>
            <div>
              <h3>Retrieve</h3>
              <p>Find the relevant evidence with hybrid search over the mailbox.</p>
            </div>
          </li>
          <li>
            <span className="landing-step-n">4</span>
            <div>
              <h3>Synthesize</h3>
              <p>Answer in plain language with citations back to source messages.</p>
            </div>
          </li>
        </ol>
      </section>

      {/* Demo / setup CTA */}
      <section className="landing-section landing-final">
        <h2 className="landing-h2">Open the demo workspace</h2>
        <p className="landing-final-sub">
          Start in the overview, then explore the network, projects, and cited
          answers. Check setup first if you are wiring up a fresh environment.
        </p>
        <div className="landing-cta-row landing-cta-center">
          <Link to="/app" className="landing-cta-primary">
            Open demo workspace
          </Link>
          <Link to="/app/status" className="landing-cta-secondary">
            Check setup
          </Link>
        </div>
      </section>

      <footer className="landing-footer">
        <span>Continuity — email continuity for coverage, handoffs, and institutional memory.</span>
      </footer>
    </div>
  );
}
