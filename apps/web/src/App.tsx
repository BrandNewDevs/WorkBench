import { useEffect, useRef, useState } from "react";
import AnimatedField from "./components/animated-field";
import Navbar from "./components/navbar";
import About from "./pages/about";
import Features from "./pages/features";
import Footer from "./components/footer";

type Route = "home" | "features" | "about";

function getHashTarget(): string {
  return window.location.hash.slice(1).split("?")[0].replace(/^\/+/, "").toLowerCase();
}

function getRoute(): Route {
  const hash = getHashTarget();
  if (hash === "features") return "features";
  if (hash === "about") return "about";
  return "home";
}

export function App() {
  const [route, setRoute] = useState<Route>(getRoute);
  const [hash, setHash] = useState(window.location.hash);
  const [activeFeature, setActiveFeature] = useState<string | null>(null);

  useEffect(() => {
    const handleHashChange = () => {
      setHash(window.location.hash);
      setRoute(getRoute());
    };
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  useEffect(() => {
    setActiveFeature(null);
    const frame = requestAnimationFrame(() => {
      if (route === "home" && getHashTarget() === "download") {
        document.getElementById("download")?.scrollIntoView({ behavior: "auto", block: "start" });
        return;
      }
      window.scrollTo({ top: 0, behavior: "auto" });
    });
    return () => cancelAnimationFrame(frame);
  }, [hash, route]);

  const isFeaturesPage = route === "features";
  const isAboutPage = route === "about";

  return (
    <>
      {route === "home" && (
        <>
          <AnimatedField />
          <div className="ambient-glow" aria-hidden="true" />
          <div className="grid-overlay" aria-hidden="true" />
        </>
      )}
      <div id="home" className="site-shell">
        <Navbar isFeaturesPage={isFeaturesPage} isAboutPage={isAboutPage} />
        <main className="site-main">
          {route === "home" && <HomePage />}
          {isFeaturesPage && <Features activeFeature={activeFeature} setActiveFeature={setActiveFeature} />}
          {isAboutPage && <About />}
        </main>
        <Footer />
      </div>
    </>
  );
}

/* ============================================================
   HOME PAGE
   ============================================================ */
function HomePage() {
  return (
    <>
      {/* Hero */}
      <section className="section hero" aria-labelledby="home-heading">
        <div className="container hero-content">
          <span className="hero-badge">
            <span className="hero-badge-dot" aria-hidden="true" />
            Private AI Workbench
          </span>
          <h1 id="home-heading">Your private AI workbench for sensitive work.</h1>
          <p className="hero-desc">
            Transform confidential documents into cited, approval-ready drafts — entirely on your local machine. No cloud. No data leaving your control.
          </p>
          <div className="hero-actions">
            <a href="https://github.com/BrandNewDevs/WorkBench" target="_blank" rel="noreferrer" className="btn-primary">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
              Download WorkBench
            </a>
            <a href="https://github.com/BrandNewDevs/WorkBench" target="_blank" rel="noreferrer" className="btn-secondary">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.374 0 0 5.373 0 12c0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23A11.509 11.509 0 0 1 12 5.803c1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576C20.566 21.797 24 17.3 24 12c0-6.627-5.373-12-12-12z"/></svg>
              View on GitHub
            </a>
          </div>
        </div>

        {/* App Mockup */}
        <div className="container">
          <div className="app-mockup">
            <div className="app-mockup-bar">
              <span className="app-mockup-dot" />
              <span className="app-mockup-dot" />
              <span className="app-mockup-dot" />
              <span className="app-mockup-url">workbench://local/workspace</span>
            </div>
            <div className="app-mockup-body">
              <div className="app-mockup-sidebar">
                <div className="app-mockup-sidebar-item active">
                  <span className="app-mockup-sidebar-icon" />
                  Documents
                </div>
                <div className="app-mockup-sidebar-item">
                  <span className="app-mockup-sidebar-icon" />
                  Knowledge Base
                </div>
                <div className="app-mockup-sidebar-item">
                  <span className="app-mockup-sidebar-icon" />
                  Drafts
                </div>
                <div className="app-mockup-sidebar-item">
                  <span className="app-mockup-sidebar-icon" />
                  Audit Log
                </div>
                <div className="app-mockup-sidebar-item">
                  <span className="app-mockup-sidebar-icon" />
                  Settings
                </div>
              </div>
              <div className="app-mockup-main">
                <div className="mockup-header">
                  <span className="mockup-title">Workspace</span>
                  <span className="mockup-status">
                    <span className="mockup-status-dot" />
                    Local model active
                  </span>
                </div>
                <div className="mockup-search">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                  Search documents...
                  <span className="mockup-search-bar" />
                </div>
                <div className="mockup-cards">
                  <div className="mockup-card">
                    <div className="mockup-card-label">Document</div>
                    <div className="mockup-card-lines">
                      <span className="mockup-line" />
                      <span className="mockup-line" />
                      <span className="mockup-line" />
                    </div>
                  </div>
                  <div className="mockup-card">
                    <div className="mockup-card-label">Retrieval</div>
                    <div className="mockup-card-lines">
                      <span className="mockup-line" />
                      <span className="mockup-line" />
                      <span className="mockup-line" />
                    </div>
                  </div>
                  <div className="mockup-ai">
                    <div className="mockup-ai-header">
                      <span className="mockup-ai-dot" />
                      <span className="mockup-ai-label">AI Assistant</span>
                    </div>
                    <div className="mockup-ai-text">
                      Analyzing document "Inspection_Report_042.pdf" — extracted 14 findings. Cross-referencing with local knowledge base. Draft response ready for review.
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
