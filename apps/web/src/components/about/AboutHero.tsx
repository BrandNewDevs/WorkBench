import { Zap } from "lucide-react";

const stats = [
  { num: "0", label: "External API Calls" },
  { num: "100%", label: "On-Premise" },
  { num: "1", label: "Workstation" },
  { num: "<2s", label: "Local Inference" },
];

export default function AboutHero() {
  return (
    <header className="about-hero">
      <h1 id="about-heading">About WorkBench</h1>
      <p className="about-hero-desc">
        A sovereign, local-first AI workbench for confidential industrial and government work.
      </p>
      <div className="about-stats">
        <div className="about-stats-header">
          <Zap size={14} aria-hidden="true" />
          <span>Key Takeaways</span>
        </div>
        <div className="about-stats-row">
          {stats.map(({ num, label }, i) => (
            <div key={label} className="about-stat-group">
              {i > 0 && <span className="about-stat-divider" aria-hidden="true" />}
              <div className="about-stat">
                <span className="about-stat-num">{num}</span>
                <span className="about-stat-label">{label}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </header>
  );
}
