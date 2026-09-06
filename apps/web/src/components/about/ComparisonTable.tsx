import AboutSection from "./AboutSection";

const rows = [
  { feature: "Data Location", good: "100% on-premise", bad: "External servers" },
  { feature: "Network Requirement", good: "Air-gapped capable", bad: "Requires internet" },
  { feature: "Data Sovereignty", good: "Complete control", bad: "Third-party dependent" },
  { feature: "Compliance", good: "Government/Industrial ready", bad: "Variable compliance" },
  { feature: "Latency", good: "Local inference", bad: "Network dependent" },
  { feature: "Cost Model", good: "One-time hardware", bad: "Recurring subscription" },
  { feature: "Citation Control", good: "Source-verified", bad: "Model-generated" },
  { feature: "Audit Trail", good: "Local, complete", bad: "Partial, external" },
];

export default function ComparisonTable() {
  return (
    <AboutSection title="Why WorkBench?" subtitle="Comparison with cloud-based AI solutions">
      <div className="comparison-table-wrapper">
        <table className="comparison-table">
          <thead>
            <tr>
              <th>Feature</th>
              <th>WorkBench (Local-First)</th>
              <th>Cloud AI Solutions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ feature, good, bad }) => (
              <tr key={feature}>
                <td>{feature}</td>
                <td className="highlight-good">{good}</td>
                <td className="highlight-bad">{bad}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </AboutSection>
  );
}
