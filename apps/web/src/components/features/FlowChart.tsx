import { useEffect, useRef, useState } from "react";
import {
  ArrowDown,
  CircleCheck,
  ClipboardCheck,
  Cpu,
  Database,
  Download,
  Eye,
  FileText,
  PenLine,
  Search,
  ShieldCheck,
  SquareCheck,
  Upload,
} from "lucide-react";

const steps = [
  { id: "upload", num: "01", title: "Upload", desc: "Add documents to your workspace", icon: <Upload size={20} strokeWidth={1.5} aria-hidden="true" /> },
  {
    id: "extract",
    num: "02",
    title: "Extract",
    desc: "Extract text and structure from documents",
    icon: <FileText size={20} strokeWidth={1.5} aria-hidden="true" />,
    branch: { title: "OCR and Vision", icon: <Eye size={14} strokeWidth={1.5} aria-hidden="true" /> },
  },
  {
    id: "search",
    num: "03",
    title: "Search",
    desc: "Retrieve relevant information using local RAG",
    icon: <Search size={20} strokeWidth={1.5} aria-hidden="true" />,
    branch: { title: "Local Retrieval", icon: <Database size={14} strokeWidth={1.5} aria-hidden="true" /> },
  },
  {
    id: "draft",
    num: "04",
    title: "Draft",
    desc: "Generate useful work with local AI",
    icon: <PenLine size={20} strokeWidth={1.5} aria-hidden="true" />,
    branch: { title: "Local AI", icon: <Cpu size={14} strokeWidth={1.5} aria-hidden="true" /> },
  },
  {
    id: "check",
    num: "05",
    title: "Check",
    desc: "Validate and review generated results",
    icon: <SquareCheck size={20} strokeWidth={1.5} aria-hidden="true" />,
    branch: { title: "Sandboxed Execution", icon: <ShieldCheck size={14} strokeWidth={1.5} aria-hidden="true" /> },
  },
  {
    id: "approve",
    num: "06",
    title: "Approve",
    desc: "Keep humans in control of important actions",
    icon: <CircleCheck size={20} strokeWidth={1.5} aria-hidden="true" />,
    branch: { title: "Approval and Audit", icon: <ClipboardCheck size={14} strokeWidth={1.5} aria-hidden="true" /> },
  },
  { id: "export", num: "07", title: "Export", desc: "Export the final reviewable document", icon: <Download size={20} strokeWidth={1.5} aria-hidden="true" /> },
];

export default function FlowChart() {
  const containerRef = useRef<HTMLDivElement>(null);
  const lineFillRef = useRef<HTMLDivElement>(null);
  const [reached, setReached] = useState(0);

  useEffect(() => {
    const el = containerRef.current;
    const lineFill = lineFillRef.current;
    if (!el || !lineFill) return;

    const handleScroll = () => {
      const track = el.querySelector(".flow-vertical-track") as HTMLElement;
      if (!track) return;

      const rect = track.getBoundingClientRect();
      const scrolled = window.innerHeight - rect.top;
      const progress = Math.min(Math.max(scrolled / rect.height, 0), 1);
      lineFill.style.height = `${progress * 100}%`;

      let next = 0;
      track.querySelectorAll<HTMLElement>(".flow-vertical-dot").forEach((dot) => {
        const step = dot.closest<HTMLElement>(".flow-vertical-step");
        if (!step) return;
        const center = step.offsetTop + dot.offsetTop + dot.offsetHeight / 2;
        if (scrolled >= center) {
          next = Math.max(next, Number(step.getAttribute("data-step-index")));
        }
      });
      setReached(next);
    };

    window.addEventListener("scroll", handleScroll, { passive: true });
    handleScroll();
    return () => window.removeEventListener("scroll", handleScroll);
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
          const visible = i <= reached;

          return (
            <div
              key={step.id}
              data-step-index={i}
              className={`flow-vertical-step ${isLeft ? "left" : "right"} ${visible ? "visible" : ""}`}
            >
              <div className={`flow-vertical-dot ${visible ? "on" : ""}`}>
                <div className="flow-vertical-dot-inner" />
                <div className="flow-vertical-dot-ring" />
              </div>

              <div className="flow-vertical-card">
                <div className="flow-vertical-card-icon">{step.icon}</div>
                <span className="flow-vertical-card-num">STEP {step.num}</span>
                <h3 className="flow-vertical-card-title">{step.title}</h3>
                <p className="flow-vertical-card-desc">{step.desc}</p>
              </div>

              {step.branch && (
                <div className="flow-branch" aria-hidden="true">
                  <span className="flow-branch-line" />
                  <span className="flow-branch-pill">
                    <span className="flow-branch-icon">{step.branch.icon}</span>
                    <span className="flow-branch-title">{step.branch.title}</span>
                  </span>
                </div>
              )}
            </div>
          );
        })}

        <div className={`flow-vertical-tail ${reached >= steps.length - 1 ? "is-on" : ""}`}>
          <span className="flow-tail-node">
            <ArrowDown size={16} strokeWidth={2} aria-hidden="true" />
          </span>
        </div>
      </div>
    </div>
  );
}
