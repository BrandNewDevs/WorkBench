import { Download } from "lucide-react";
import AppMockup from "./AppMockup";

export default function Hero() {
  return (
    <section className="section hero" aria-labelledby="home-heading">
      <div className="container hero-content">
        <h1 id="home-heading">
          Your private AI workbench
          <br className="hero-linebreak" />
          for sensitive work.
        </h1>
        <p className="hero-desc">
          Turn confidential documents into cited, approval-ready drafts — entirely on your machine. No cloud. Nothing leaves your control.
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
