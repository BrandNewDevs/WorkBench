import { useEffect, useRef, useState } from "react";
import type { JSX } from "react";
import { Bot, CircleCheck, Eye, FileText, Lock, Search } from "lucide-react";
import FeatureCard from "./FeatureCard";
import FeatureModal, { type Feature } from "./FeatureModal";

const featureDetails = [
  { id: "01", title: "Local AI", category: "AI CAPABILITIES", desc: "Run inference entirely on-device. No data leaves your machine. WorkBench uses local model execution for confidential work." },
  { id: "02", title: "Document Workflow", category: "WORKFLOW", desc: "Organize inspection documents, uploads, and generated drafts in one local workspace." },
  { id: "03", title: "Local Retrieval", category: "KNOWLEDGE", desc: "Search an offline knowledge base. Retrieved source metadata keeps citations tied to the documents on your hardware." },
  { id: "04", title: "Sandboxed Execution", category: "SECURITY", desc: "Run coding tasks in isolated Docker containers with no network access and a temporary task-folder mount." },
  { id: "05", title: "OCR and Vision", category: "VISION", desc: "Process scanned pages and site photographs with local OCR and vision models, without uploading them." },
  { id: "06", title: "Approval and Audit", category: "GOVERNANCE", desc: "Review drafts before export. Session logs and approval records keep the workflow traceable." },
] as const satisfies readonly (Feature & { id: string; category: string })[];

const featureIcons: Record<(typeof featureDetails)[number]["id"], JSX.Element> = {
  "01": <Bot size={18} aria-hidden="true" />,
  "02": <FileText size={18} aria-hidden="true" />,
  "03": <Search size={18} aria-hidden="true" />,
  "04": <Lock size={18} aria-hidden="true" />,
  "05": <Eye size={18} aria-hidden="true" />,
  "06": <CircleCheck size={18} aria-hidden="true" />,
};

export default function FeatureList() {
  const [activeFeatureId, setActiveFeatureId] = useState<string | null>(null);
  const [visibleCards, setVisibleCards] = useState<Set<number>>(new Set());
  const listRef = useRef<HTMLUListElement>(null);
  const activeData = activeFeatureId ? (featureDetails.find((f) => f.id === activeFeatureId) ?? null) : null;

  useEffect(() => {
    const el = listRef.current;
    if (!el) return;

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          const idx = Number(entry.target.getAttribute("data-card-index"));
          if (entry.isIntersecting) {
            setVisibleCards((prev) => new Set([...prev, idx]));
          }
        });
      },
      { threshold: 0.05, rootMargin: "0px 0px -10px 0px" }
    );

    const cardEls = el.querySelectorAll("[data-card-index]");
    cardEls.forEach((cardEl) => observer.observe(cardEl));

    return () => observer.disconnect();
  }, []);

  return (
    <>
      <ul className="feature-list" aria-label="WorkBench features" ref={listRef}>
        {featureDetails.map(({ id, title, category, desc }, i) => (
          <li
            key={id}
            data-card-index={i}
            className={`feature-list-item ${i % 2 === 0 ? "from-left" : "from-right"} ${visibleCards.has(i) ? "visible" : ""}`}
          >
            <FeatureCard
              id={id}
              title={title}
              category={category}
              desc={desc}
              icon={featureIcons[id]}
              onClick={setActiveFeatureId}
            />
          </li>
        ))}
      </ul>

      {activeData && <FeatureModal feature={activeData} onClose={() => setActiveFeatureId(null)} />}
    </>
  );
}
