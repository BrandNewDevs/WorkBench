import { useEffect, useRef, useState } from "react";

const featureDetails = [
  { id: "01", title: "Local AI", desc: "Run inference entirely on-device. No data leaves your machine — full privacy with local model execution." },
  { id: "02", title: "Other Features", desc: "Document tagging and organization that keeps your workflow structured and searchable." },
  { id: "03", title: "Local AI", desc: "Offline embeddings and retrieval. Your knowledge base stays on your hardware at all times." },
  { id: "04", title: "Other Features", desc: "Sandboxed code execution for safe experimentation within isolated Docker containers." },
  { id: "05", title: "Local AI", desc: "On-device OCR and vision processing. Scan and interpret documents without uploading anything." },
  { id: "06", title: "Other Features", desc: "Audit trail and session logs that record every action for compliance and transparency." },
] as const;

function AnimatedField() {
  return (
    <div className="motion-field" aria-hidden="true">
      <svg className="flow-lines" viewBox="0 0 700 500" preserveAspectRatio="xMidYMid slice">
        <defs>
          <linearGradient id="flowGradient" x1="0" x2="1">
            <stop offset="0" stopColor="#8d7cff" stopOpacity=".1" />
            <stop offset=".5" stopColor="#8d7cff" />
            <stop offset="1" stopColor="#ff9a6e" />
          </linearGradient>
        </defs>
        <g fill="none" stroke="url(#flowGradient)" strokeWidth="1.2">
          <path d="M-40 60 C170 90 230 230 350 250 S530 130 740 70" />
          <path d="M-40 100 C170 120 230 235 350 250 S530 155 740 100" />
          <path d="M-40 140 C165 150 240 240 350 250 S535 180 740 130" />
          <path d="M-40 180 C165 180 245 245 350 250 S540 205 740 165" />
          <path d="M-40 220 C170 215 250 248 350 250 S540 225 740 200" />
          <path d="M-40 280 C170 285 250 252 350 250 S540 275 740 300" />
          <path d="M-40 320 C165 320 245 258 350 250 S535 295 740 335" />
          <path d="M-40 360 C165 350 240 260 350 250 S530 320 740 370" />
          <path d="M-40 400 C170 380 230 265 350 250 S530 345 740 405" />
          <path d="M-40 440 C170 410 230 270 350 250 S530 370 740 440" />
        </g>
      </svg>
      <div className="motion-grid" />
    </div>
  );
}

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

  const openFeature = (id: string) => setActiveFeature(id);
  const closeFeature = () => setActiveFeature(null);

  const activeData = activeFeature ? featureDetails.find(f => f.id === activeFeature) : null;

  return (
    <>
      {!isFeaturesPage && <AnimatedField />}
      <main ref={shellRef} className="site-shell min-h-screen">
        <nav aria-label="Main navigation" className="main-navigation flex w-[calc(100%-2rem)] items-center gap-3 rounded-full bg-[#0c0c0c]/75 px-3 py-3 backdrop-blur-xl sm:w-[calc(100%-5rem)]">
          <a href="/" className="flex shrink-0 items-center gap-3 pl-1" aria-label="WorkBench home">
            <span className="relative grid size-10 place-items-center overflow-hidden rounded-[11px] border border-[#777] bg-[#e9e5e1] text-[#161616]" aria-hidden="true">
              <span className="absolute left-1 top-1 size-2 border-l-2 border-t-2 border-[#161616]" />
              <span className="relative text-[16px] font-black leading-none tracking-[-0.2em]">WB</span>
              <span className="absolute bottom-1 right-1 size-2 border-b-2 border-r-2 border-[#161616]" />
            </span>
            <span className="brand-name hidden text-xl font-bold text-[#f0ece8] sm:inline">WORKBENCH</span>
          </a>
          <div className="mx-auto flex items-center gap-4 px-2 text-xs text-[#aaa] sm:gap-8 sm:text-sm">
            {isFeaturesPage && <a href="/" className="transition hover:text-white">Home</a>}
            <a href="https://github.com/BrandNewDevs/WorkBench" target="_blank" rel="noreferrer" className="transition hover:text-white">Github</a>
            <a href="/features" aria-current={isFeaturesPage ? "page" : undefined} className="transition hover:text-white">Features</a>
            <a href="#about" className="transition hover:text-white">About</a>
          </div>
          <a href="#download" className="shrink-0 rounded-full bg-[#e9e5e1] px-4 py-2.5 text-sm font-medium text-[#161616] transition hover:bg-white">Download</a>
        </nav>
        <section id="top" className={`hero-panel ${isFeaturesPage ? "features-page" : ""}`} aria-label={isFeaturesPage ? "WorkBench features" : "WorkBench home"}>
          {!isFeaturesPage && (
            <div className="home-content" />
          )}
          {isFeaturesPage && (
            <div className="features-content">
              <div className="feature-list">
                {featureDetails.map(({ id, title, desc }) => (
                  <article className="feature-card" key={id} onClick={() => openFeature(id)} role="button" tabIndex={0} aria-label={`View ${title} details`} onKeyDown={(e) => { if (e.key === "Enter") openFeature(id); }}>
                    <span>{title === "Local AI" ? "Local AI" : "Other Features"}</span>
                    <h2>{title}</h2>
                  </article>
                ))}
              </div>
            </div>
          )}
        </section>
      </main>
      {activeFeature && (
        <div className="feature-overlay" onClick={closeFeature} role="dialog" aria-modal="true">
          <div className="feature-modal" onClick={(e) => e.stopPropagation()}>
            <button className="feature-modal-close" onClick={closeFeature} aria-label="Close">
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M1 1L13 13M1 13L13 1" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
            </button>
            <span className="feature-modal-tag">{activeData?.title === "Local AI" ? "Local AI" : "Other Features"}</span>
            <h2 className="feature-modal-title">{activeData?.title}</h2>
            <p className="feature-modal-desc">{activeData?.desc}</p>
          </div>
        </div>
      )}
    </>
  );
}
