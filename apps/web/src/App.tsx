import { useEffect, useState } from "react";
import AnimatedField from "./components/animated-field";
import Navbar from "./components/Navbar";
import About from "./pages/About";
import Features from "./pages/Features";
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
      const target = getHashTarget();
      if (route === "home" && target === "download") {
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
          <h1 id="home-heading">Your private AI workbench for sensitive work.</h1>
          <p className="hero-desc">
            Transform confidential documents into cited, approval-ready drafts — entirely on your local machine. No cloud. No data leaving your control.
          </p>
          <div className="hero-actions" style={{ justifyContent: "center" }}>
            <a href="https://github.com/BrandNewDevs/WorkBench" target="_blank" rel="noreferrer" className="btn-primary">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
              Download WorkBench
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

      {/* Download */}
      <section id="download" className="section" aria-labelledby="download-heading">
        <div className="container" style={{ textAlign: "center", maxWidth: 600 }}>
          <h2 id="download-heading" style={{ fontSize: "clamp(1.8rem, 4vw, 2.5rem)", fontWeight: 600, letterSpacing: "-0.04em", margin: "0 0 1rem" }}>
            Built for one secure workstation.
          </h2>
          <p style={{ color: "var(--text-secondary)", fontSize: "0.95rem", lineHeight: 1.65, margin: "0 0 1.5rem" }}>
            WorkBench is in active MVP development. The Windows Electron client and its local services are not available as a public installer yet.
          </p>
          <a href="https://github.com/BrandNewDevs/WorkBench" target="_blank" rel="noreferrer" className="btn-secondary">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="#FFD700" stroke="#FFD700" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
            Star on GitHub
          </a>
        </div>
      </section>
    </>
  );
}
