import { useEffect, useRef, useState } from "react";
import AnimatedField from "./components/AnimatedField";
import Navbar from "./components/Navbar";
import Features from "./pages/Features";

export function App() {
  const shellRef = useRef<HTMLElement>(null);
  const isFeaturesPage = window.location.pathname === "/features";
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
      {!isFeaturesPage && <AnimatedField />}
      <main ref={shellRef} className="site-shell min-h-screen">
        <Navbar isFeaturesPage={isFeaturesPage} />
        <section id="top" className={`hero-panel ${isFeaturesPage ? "features-page" : ""}`} aria-label={isFeaturesPage ? "WorkBench features" : "WorkBench home"}>
          {!isFeaturesPage && <div className="home-content" />}
          {isFeaturesPage && <Features activeFeature={activeFeature} setActiveFeature={setActiveFeature} />}
        </section>
      </main>
    </>
  );
}