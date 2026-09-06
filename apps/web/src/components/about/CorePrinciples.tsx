import { Shield, WifiOff, CheckCircle, FileText } from "lucide-react";
import AboutSection from "./AboutSection";

const principles = [
  {
    icon: Shield,
    title: "Local-First Architecture",
    desc: "All inference, embeddings, OCR/vision, retrieval, tool execution, artifacts, logs, and session data remain on the local machine. No cloud APIs, no remote fallbacks, no telemetry.",
  },
  {
    icon: WifiOff,
    title: "Air-Gapped Operation",
    desc: "Designed to work in completely isolated environments with zero external connections. Zero external API use with live network verification.",
  },
  {
    icon: CheckCircle,
    title: "Approval-Gated Workflow",
    desc: "Human oversight at every critical step. The AI proposes; deterministic logic enforces routing, validation, and permission checks. Users approve all side effects.",
  },
  {
    icon: FileText,
    title: "Evidence-Based Output",
    desc: "Citations are application-controlled from source metadata. Artifacts are labeled as drafts until approval. The system states uncertainty rather than inventing conclusions.",
  },
];

export default function CorePrinciples() {
  return (
    <AboutSection title="Core Principles">
      <div className="about-two-col">
        {principles.map(({ icon: Icon, title, desc }) => (
          <article key={title} className="about-text-block">
            <div className="about-text-header">
              <Icon size={18} className="about-text-icon" aria-hidden="true" />
              <span className="about-text-label">{title}</span>
            </div>
            <p>{desc}</p>
          </article>
        ))}
      </div>
    </AboutSection>
  );
}
