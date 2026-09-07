import { Check, X } from "lucide-react";

const good = [
  "100% on-premise data",
  "Air-gapped capable",
  "Complete data sovereignty",
  "Government/Industrial ready",
  "Local inference",
  "One-time hardware cost",
  "Source-verified citations",
  "Complete local audit trail",
];

const bad = [
  "External server storage",
  "Requires internet",
  "Third-party dependent",
  "Variable compliance",
  "Network dependent",
  "Recurring subscription",
  "Model-generated citations",
  "Partial external audit",
];

export default function WhyWorkbench() {
  return (
    <div className="why-workbench">
      <div className="why-workbench-header">
        <h2 className="section-title why-workbench-title">Why WorkBench?</h2>
        <p className="why-workbench-desc">The difference comes down to where your data lives and who can see it.</p>
      </div>
      <div className="comparison-grid">
        <div className="comparison-col comparison-col--good">
          <span className="comparison-col-header">WorkBench</span>
          <ul className="comparison-list">
            {good.map((item) => (
              <li key={item}>
                <Check size={14} className="comparison-icon comparison-icon--good" aria-hidden="true" />
                {item}
              </li>
            ))}
          </ul>
        </div>
        <div className="comparison-col comparison-col--bad">
          <span className="comparison-col-header">Cloud AI</span>
          <ul className="comparison-list">
            {bad.map((item) => (
              <li key={item}>
                <X size={14} className="comparison-icon comparison-icon--bad" aria-hidden="true" />
                {item}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
