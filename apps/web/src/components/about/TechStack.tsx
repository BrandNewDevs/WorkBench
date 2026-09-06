import { useEffect, useRef } from "react";
import { Monitor, Server, BrainCircuit, Layers, Database, Box } from "lucide-react";
import AboutSection from "./AboutSection";

const stackItems = [
  { icon: Monitor, label: "Client", name: "Electron + React + TypeScript" },
  { icon: Server, label: "Backend", name: "Python + FastAPI" },
  { icon: BrainCircuit, label: "AI Runtime", name: "Ollama (Qwen3 / Qwen3-VL)" },
  { icon: Layers, label: "Vector Store", name: "Chroma (local)" },
  { icon: Database, label: "Database", name: "SQLite" },
  { icon: Box, label: "Sandbox", name: "Docker (network-disabled)" },
];

export default function TechStack() {
  const trackRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = trackRef.current?.closest(".tech-carousel");
    if (!el) return;
    const io = new IntersectionObserver(
      ([entry]) => {
        if (trackRef.current) {
          trackRef.current.style.animationPlayState = entry.isIntersecting ? "running" : "paused";
        }
      },
      { threshold: 0 }
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <AboutSection title="Technology">
      <div className="tech-carousel">
        <div className="tech-track" ref={trackRef} style={{ animationPlayState: "paused" }}>
          {[...stackItems, ...stackItems].map(({ icon: Icon, label, name }, i) => (
            <div key={`${label}-${i}`} className="tech-card">
              <div className="tech-card-icon"><Icon size={22} strokeWidth={1.5} /></div>
              <span className="tech-card-label">{label}</span>
              <span className="tech-card-name">{name}</span>
            </div>
          ))}
        </div>
      </div>
    </AboutSection>
  );
}
