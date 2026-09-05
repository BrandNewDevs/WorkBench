import { useEffect, useRef, useState } from "react";
import AnimatedField from "./components/AnimatedField";
import Navbar from "./components/Navbar";
import Features from "./pages/Features";
import About from "./pages/About";

export function App() {
  const shellRef = useRef<HTMLElement>(null);
  const pathname = window.location.pathname;
  const isFeaturesPage = pathname === "/features";
  const isAboutPage = pathname === "/about";
  const isPage = isFeaturesPage || isAboutPage;
  const [activeFeature, setActiveFeature] = useState<string | null>(null);

  useEffect(() => {
    const shell = shellRef.current;
    if (!shell || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    let frame = 0;
    const movePanel = (event: PointerEvent) => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const x = (event.clientX / window.innerWidth - 0.5) * 5;
        const y = (event.clientY / window.innerHeight - 0.5) * 4;
        shell.style.transform = `translate3d(${x}px, ${y}px, 0)`;
      });
    };
    const resetPanel = () => {
      shell.style.transform = "translate3d(0, 0, 0)";
    };

    window.addEventListener("pointermove", movePanel);
    window.addEventListener("pointerleave", resetPanel);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("pointermove", movePanel);
      window.removeEventListener("pointerleave", resetPanel);
    };
  }, []);

  return (
    <>
      {!isPage && <AnimatedField />}
      <main ref={shellRef} className="site-shell min-h-screen">
        <Navbar isFeaturesPage={isFeaturesPage} isAboutPage={isAboutPage} />
        <section id="top" className={`hero-panel ${isPage ? "features-page" : ""}`} aria-label={isFeaturesPage ? "WorkBench features" : isAboutPage ? "About WorkBench" : "WorkBench home"}>
          {!isPage && <div className="home-content" />}
          {isFeaturesPage && <Features activeFeature={activeFeature} setActiveFeature={setActiveFeature} />}
          {isAboutPage && <About />}
        </section>
      </main>
    </>
  );
}