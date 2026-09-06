import { useEffect, useRef, useState } from "react";
import { CircleCheck, Download, FileText, PenLine, Search, SquareCheck, Upload } from "lucide-react";

const steps = [
  { id: "upload", num: "01", title: "Upload", desc: "Add documents to your workspace", icon: <Upload size={20} strokeWidth={1.5} aria-hidden="true" /> },
  { id: "extract", num: "02", title: "Extract", desc: "Extract text and structure from documents", icon: <FileText size={20} strokeWidth={1.5} aria-hidden="true" /> },
  { id: "search", num: "03", title: "Search", desc: "Retrieve relevant information using local RAG", icon: <Search size={20} strokeWidth={1.5} aria-hidden="true" /> },
  { id: "draft", num: "04", title: "Draft", desc: "Generate useful work with local AI", icon: <PenLine size={20} strokeWidth={1.5} aria-hidden="true" /> },
  { id: "check", num: "05", title: "Check", desc: "Validate and review generated results", icon: <SquareCheck size={20} strokeWidth={1.5} aria-hidden="true" /> },
  { id: "approve", num: "06", title: "Approve", desc: "Keep humans in control of important actions", icon: <CircleCheck size={20} strokeWidth={1.5} aria-hidden="true" /> },
  { id: "export", num: "07", title: "Export", desc: "Export the final reviewable document", icon: <Download size={20} strokeWidth={1.5} aria-hidden="true" /> },
];

export default function FlowChart() {
  const containerRef = useRef<HTMLDivElement>(null);
  const lineFillRef = useRef<HTMLDivElement>(null);
  const [visibleSteps, setVisibleSteps] = useState<Set<number>>(new Set());

  useEffect(() => {
    const el = containerRef.current;
    const lineFill = lineFillRef.current;
    if (!el || !lineFill) return;

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

    const handleScroll = () => {
      const track = el.querySelector(".flow-vertical-track") as HTMLElement;
      if (!track) return;
      const rect = track.getBoundingClientRect();
      const trackTop = rect.top;
      const trackHeight = rect.height;
      const viewportHeight = window.innerHeight;
      const scrolled = viewportHeight - trackTop;
      const progress = Math.min(Math.max(scrolled / trackHeight, 0), 1);
      lineFill.style.height = `${progress * 100}%`;
    };

    window.addEventListener("scroll", handleScroll, { passive: true });
    handleScroll();

    return () => {
      observer.disconnect();
      window.removeEventListener("scroll", handleScroll);
    };
  }, []);

  return (
    <div className="flow-vertical" ref={containerRef}>
      <div className="flow-vertical-header">
        <span className="flowchart-badge">Workflow</span>
      </div>

      <div className="flow-vertical-track">
        <div className="flow-vertical-line" />
        <div className="flow-vertical-line-fill" ref={lineFillRef} />

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
