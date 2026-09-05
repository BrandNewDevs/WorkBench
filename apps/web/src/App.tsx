import { useEffect, useRef, useState } from "react";
import AnimatedField from "./components/AnimatedField";
import Navbar from "./components/Navbar";
import About from "./pages/About";
import Features from "./pages/Features";

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
  const homePanelRef = useRef<HTMLElement>(null);
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

  useEffect(() => {
    const homePanel = homePanelRef.current;
    if (route !== "home" || !homePanel || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    let frame = 0;
    const movePanel = (event: PointerEvent) => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const x = (event.clientX / window.innerWidth - 0.5) * 5;
        const y = (event.clientY / window.innerHeight - 0.5) * 4;
        homePanel.style.transform = `translate3d(${x}px, ${y}px, 0)`;
      });
    };
    const resetPanel = () => {
      homePanel.style.transform = "translate3d(0, 0, 0)";
    };

    window.addEventListener("pointermove", movePanel);
    window.addEventListener("pointerleave", resetPanel);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("pointermove", movePanel);
      window.removeEventListener("pointerleave", resetPanel);
    };
  }, [route]);

  const isFeaturesPage = route === "features";
  const isAboutPage = route === "about";

  return (
    <>
      {route === "home" && <AnimatedField />}
      <div id="home" className="site-shell">
        <Navbar isFeaturesPage={isFeaturesPage} isAboutPage={isAboutPage} />
        <main className="site-main">
          {route === "home" && (
            <>
              <section ref={homePanelRef} id="top" className="hero-panel home-page" aria-labelledby="home-heading">
                <div className="home-content">
                  <p className="eyebrow">Air-gapped AI workbench</p>
                  <h1 id="home-heading">Private work, kept local.</h1>
                  <p className="home-description">
                    Turn confidential inspection documents into cited, approval-ready drafts without sending your data to a cloud service.
                  </p>
                  <div className="hero-actions">
                    <a href="#features" className="primary-action">Explore features</a>
                    <a href="#download" className="secondary-action">Availability</a>
                  </div>
                </div>
              </section>
              <section id="download" className="availability-section" aria-labelledby="download-heading">
                <div className="availability-content">
                  <p className="eyebrow">Installation</p>
                  <h2 id="download-heading">Built for one secure workstation.</h2>
                  <p>
                    WorkBench is in active MVP development. The Windows Electron client and its local FastAPI, Ollama, Chroma, SQLite, and Docker services are not available as a public installer yet.
                  </p>
                  <a className="secondary-action" href="https://github.com/BrandNewDevs/WorkBench" target="_blank" rel="noreferrer">
                    Follow development on GitHub
                  </a>
                </div>
              </section>
            </>
          )}
          {isFeaturesPage && <Features activeFeature={activeFeature} setActiveFeature={setActiveFeature} />}
          {isAboutPage && <About />}
        </main>
      </div>
    </>
  );
}
