import { useEffect, useRef, useState } from "react";

const steps = [
  { id: "upload", num: "01", title: "Upload", desc: "Add documents to your workspace", icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg> },
  { id: "extract", num: "02", title: "Extract", desc: "Extract text and structure from documents", icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg> },
  { id: "search", num: "03", title: "Search", desc: "Retrieve relevant information using local RAG", icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg> },
  { id: "draft", num: "04", title: "Draft", desc: "Generate useful work with local AI", icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg> },
  { id: "check", num: "05", title: "Check", desc: "Validate and review generated results", icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg> },
  { id: "approve", num: "06", title: "Approve", desc: "Keep humans in control of important actions", icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg> },
  { id: "export", num: "07", title: "Export", desc: "Export the final reviewable document", icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> },
];

export default function FlowChart() {
  const containerRef = useRef<HTMLDivElement>(null);
  const [visibleSteps, setVisibleSteps] = useState<Set<number>>(new Set());

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          const idx = Number(entry.target.getAttribute("data-step-index"));
          if (entry.isIntersecting) {
            setVisibleSteps((prev) => new Set([...prev, idx]));
          }
        });
      },
      { threshold: 0.3, rootMargin: "0px 0px -50px 0px" }
    );

    const stepEls = el.querySelectorAll("[data-step-index]");
    stepEls.forEach((stepEl) => observer.observe(stepEl));

    return () => observer.disconnect();
  }, []);

  return (
    <div className="flow-vertical" ref={containerRef}>
      <div className="flow-vertical-header">
        <span className="flowchart-badge">Workflow</span>
        <h2 className="flowchart-title">How It Works</h2>
        <p className="flowchart-sub">From upload to finished document in seven steps.</p>
      </div>

      <div className="flow-vertical-track">
        <div className="flow-vertical-line" />
        <div className="flow-vertical-line-fill" />

        {steps.map((step, i) => {
          const isLeft = i % 2 === 0;
          const isVisible = visibleSteps.has(i);

          return (
            <div
              key={step.id}
              data-step-index={i}
              className={`flow-vertical-step ${isLeft ? "left" : "right"} ${isVisible ? "visible" : ""}`}
            >
              <div className="flow-vertical-dot">
                <div className="flow-vertical-dot-inner" />
                <div className="flow-vertical-dot-ring" />
              </div>

              <div className="flow-vertical-card">
                <div className="flow-vertical-card-icon">{step.icon}</div>
                <span className="flow-vertical-card-num">STEP {step.num}</span>
                <h3 className="flow-vertical-card-title">{step.title}</h3>
                <p className="flow-vertical-card-desc">{step.desc}</p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
