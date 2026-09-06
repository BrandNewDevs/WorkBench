import { Check, X } from "lucide-react";
import AboutSection from "./AboutSection";

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

export default function ComparisonPanel() {
  return (
    <AboutSection title="Why WorkBench?">
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
    </AboutSection>
  );
}
