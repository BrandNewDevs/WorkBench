import { useEffect, useState } from "react";
import Navbar from "./components/layout/Navbar";
import Footer from "./components/layout/Footer";
import Home from "./pages/Home";
import Features from "./pages/Features";
import About from "./pages/About";

type Route = "home" | "features" | "about";

function hashTarget(hash: string): string {
  return hash.slice(1).split("?")[0].replace(/^\/+/, "").toLowerCase();
}

function routeFromHash(hash: string): Route {
  const target = hashTarget(hash);
  if (target === "features") return "features";
  if (target === "about") return "about";
  return "home";
}

export function App() {
  const [hash, setHash] = useState(() => window.location.hash);

  useEffect(() => {
    const handleHashChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      if (routeFromHash(hash) === "home" && hashTarget(hash) === "download") {
        document.getElementById("download")?.scrollIntoView({ behavior: "auto", block: "start" });
        return;
      }
      window.scrollTo({ top: 0, behavior: "auto" });
    });
    return () => cancelAnimationFrame(frame);
  }, [hash]);

  const route = routeFromHash(hash);

  return (
    <>
      {route === "home" && (
        <>
          <div className="ambient-glow" aria-hidden="true" />
          <div className="grid-overlay" aria-hidden="true" />
        </>
      )}
      <div id="home" className="site-shell">
        <Navbar isFeaturesPage={route === "features"} isAboutPage={route === "about"} />
        <main className="site-main">
          {route === "home" && <Home />}
          {route === "features" && <Features />}
          {route === "about" && <About />}
        </main>
        <Footer />
      </div>
    </>
  );
}
