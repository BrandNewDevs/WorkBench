import AboutSection from "./AboutSection";

const outcomes = [
  { number: "0", label: "External API Calls", desc: "Complete data sovereignty with zero cloud dependencies" },
  { number: "100%", label: "On-Premise", desc: "All processing happens on your local workstation" },
  { number: "1", label: "Workstation", desc: "Single-machine deployment for the entire stack" },
  { number: "<2s", label: "Local Inference", desc: "Fast response times with local model execution" },
];

export default function KeyOutcomes() {
  return (
    <AboutSection title="Key Outcomes">
      <div className="outcomes-grid">
        {outcomes.map(({ number, label, desc }) => (
          <article key={label} className="outcome-card">
            <span className="outcome-number">{number}</span>
            <span className="outcome-label">{label}</span>
            <p>{desc}</p>
          </article>
        ))}
      </div>
    </AboutSection>
  );
}
