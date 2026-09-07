import { Star } from "lucide-react";
import { useInView } from "../../hooks/useInView";

export default function DownloadSection() {
  const { ref, inView } = useInView<HTMLDivElement>();

  return (
    <section id="download" className="section" aria-labelledby="download-heading">
      <div ref={ref} className={`container download-block${inView ? " is-visible" : ""}`}>
        <h2 id="download-heading" className="download-title reveal">
          Built for one secure workstation.
        </h2>
        <p className="download-desc reveal">
          WorkBench is in active MVP development. The Windows Electron client and its local services are not available as a public installer yet.
        </p>
        <a href="https://github.com/BrandNewDevs/WorkBench" target="_blank" rel="noreferrer" className="btn-secondary reveal">
          <Star size={16} fill="#FFD700" stroke="#FFD700" aria-hidden="true" />
          Star on GitHub
        </a>
      </div>
    </section>
  );
}
