import AboutSection from "./AboutSection";

const principles = [
  {
    title: "Local-First Architecture",
    desc: "All inference, embeddings, OCR/vision, retrieval, tool execution, artifacts, logs, and session data remain on the local machine or approved organization infrastructure. No cloud AI APIs, no remote fallbacks, no telemetry.",
  },
  {
    title: "Air-Gapped Operation",
    desc: "Designed to work in completely isolated environments with zero external connections. The system demonstrates zero external API use and provides live network verification.",
  },
  {
    title: "Approval-Gated Workflow",
    desc: "Human oversight at every critical step. The AI proposes; deterministic workflow logic enforces routing eligibility, task stages, permission checks, and validation. Users approve all side effects before execution.",
  },
  {
    title: "Evidence-Based Output",
    desc: "Citations are application-controlled from retrieved source metadata. Generated artifacts are labeled as drafts until user approval. The system states uncertainty rather than inventing conclusions.",
  },
];

export default function CorePrinciples() {
  return (
    <AboutSection title="Core Principles">
      <div className="principles-grid">
        {principles.map(({ title, desc }) => (
          <article key={title} className="about-card">
            <h3>{title}</h3>
            <p>{desc}</p>
          </article>
        ))}
      </div>
    </AboutSection>
  );
}
