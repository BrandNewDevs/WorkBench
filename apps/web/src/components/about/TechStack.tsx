import AboutSection from "./AboutSection";

const stackItems = [
  { category: "Client", name: "Electron + React + TypeScript" },
  { category: "Backend", name: "Python + FastAPI" },
  { category: "AI Runtime", name: "Ollama (Qwen3 / Qwen3-VL)" },
  { category: "Vector Store", name: "Chroma (local)" },
  { category: "Database", name: "SQLite" },
  { category: "Sandbox", name: "Docker (network-disabled)" },
];

export default function TechStack() {
  return (
    <AboutSection title="Technology Stack">
      <div className="tech-stack">
        {stackItems.map(({ category, name }) => (
          <div key={category} className="tech-item">
            <span className="tech-category">{category}</span>
            <span className="tech-name">{name}</span>
          </div>
        ))}
      </div>
    </AboutSection>
  );
}
