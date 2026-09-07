import { ShieldCheck, Lock, FileSearch, ClipboardCheck } from "lucide-react";

const takeaways = [
  {
    icon: ShieldCheck,
    title: "Sovereign by design",
    desc: "Zero external calls, no telemetry.",
  },
  {
    icon: Lock,
    title: "Confidential by default",
    desc: "Your data never leaves the machine.",
  },
  {
    icon: FileSearch,
    title: "Answers you can verify",
    desc: "Every answer cites its local source.",
  },
  {
    icon: ClipboardCheck,
    title: "Asks before it acts",
    desc: "Prompts before it runs, saves, or exports.",
  },
];

export default function AboutHero() {
  return (
    <header className="about-hero">
      <h1 id="about-heading">About WorkBench</h1>
      <p className="about-hero-desc">
        A sovereign, local-first AI workbench for confidential industrial and government work.
      </p>
      <div className="takeaways">
        <div className="takeaways-header">
          <ShieldCheck size={14} aria-hidden="true" />
          <span>Key Takeaways</span>
        </div>
        <div className="takeaways-row">
          {takeaways.map(({ icon: Icon, title, desc }, i) => (
            <div key={title} className="takeaway-group">
              {i > 0 && <span className="takeaway-divider" aria-hidden="true" />}
              <div className="takeaway">
                <div className="takeaway-icon" aria-hidden="true">
                  <Icon size={16} />
                </div>
                <div className="takeaway-body">
                  <span className="takeaway-title">{title}</span>
                  <p className="takeaway-desc">{desc}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </header>
  );
}
