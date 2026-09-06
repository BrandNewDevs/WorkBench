import { useEffect, useRef } from "react";
import { Layers } from "lucide-react";
import type { IconType } from "react-icons";
import {
  SiDocker,
  SiElectron,
  SiFastapi,
  SiOllama,
  SiSqlite,
} from "react-icons/si";
import AboutSection from "./AboutSection";

const stackItems: ReadonlyArray<{ icon: IconType; name: string; color: string }> = [
  { icon: SiElectron, name: "Electron + React + TypeScript", color: "#47848f" },
  { icon: SiFastapi, name: "Python + FastAPI", color: "#009688" },
  { icon: SiOllama, name: "Ollama (Qwen3 / Qwen3-VL)", color: "#ffffff" },
  { icon: Layers, name: "Chroma (local)", color: "#ff6f61" },
  { icon: SiSqlite, name: "SQLite", color: "#003b57" },
  { icon: SiDocker, name: "Docker (network-disabled)", color: "#2496ed" },
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
          {[...stackItems, ...stackItems].map(({ icon: Icon, name, color }, i) => (
            <div key={`${name}-${i}`} className="tech-card">
              <div className="tech-card-icon" style={{ color }}>
                <Icon size={28} aria-hidden="true" />
              </div>
              <span className="tech-card-name">{name}</span>
            </div>
          ))}
        </div>
      </div>
    </AboutSection>
  );
}
