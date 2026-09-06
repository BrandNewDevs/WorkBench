import { Download } from "lucide-react";
import AppMockup from "./AppMockup";

export default function Hero() {
  return (
    <section className="section hero" aria-labelledby="home-heading">
      <div className="container hero-content">
        <h1 id="home-heading">
          Local AI
          <br className="hero-linebreak" />
          {" "}for confidential work.
        </h1>
        <p className="hero-desc">
          An agentic AI workbench for confidential industrial and government work. Everything runs on your workstation, answers cite their sources, and the app asks before it runs, saves, or exports anything.
        </p>
        <div className="hero-actions">
          <a href="https://github.com/BrandNewDevs/WorkBench" target="_blank" rel="noreferrer" className="btn-primary">
            <span className="btn-icon" aria-hidden="true">
              <Download size={16} />
            </span>
            Download
          </a>
        </div>
      </div>

      <div className="container">
        <AppMockup />
      </div>
    </section>
  );
}
