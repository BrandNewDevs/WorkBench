import { Star } from "lucide-react";

export default function DownloadSection() {
  return (
    <section id="download" className="section" aria-labelledby="download-heading">
      <div className="container download-block">
        <h2 id="download-heading" className="download-title">
          Built for one secure workstation.
        </h2>
        <p className="download-desc">
          WorkBench is in active MVP development. The Windows Electron client and its local services are not available as a public installer yet.
        </p>
        <a href="https://github.com/BrandNewDevs/WorkBench" target="_blank" rel="noreferrer" className="btn-secondary">
          <Star size={16} fill="#FFD700" stroke="#FFD700" aria-hidden="true" />
          Star on GitHub
        </a>
      </div>
    </section>
  );
}
