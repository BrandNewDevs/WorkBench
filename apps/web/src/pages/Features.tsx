import FeatureCard from "../components/FeatureCard";
import FeatureModal, { type Feature } from "../components/FeatureModal";

const featureDetails = [
  { id: "01", title: "Local AI", desc: "Run inference entirely on-device. No data leaves your machine. WorkBench uses local model execution for confidential work." },
  { id: "02", title: "Document workflow", desc: "Organize inspection documents, uploads, and generated drafts in one local workspace." },
  { id: "03", title: "Local retrieval", desc: "Search an offline knowledge base. Retrieved source metadata keeps citations tied to the documents on your hardware." },
  { id: "04", title: "Sandboxed execution", desc: "Run coding tasks in isolated Docker containers with no network access and a temporary task-folder mount." },
  { id: "05", title: "OCR and vision", desc: "Process scanned pages and site photographs with local OCR and vision models, without uploading them." },
  { id: "06", title: "Approval and audit", desc: "Review drafts before export. Session logs and approval records keep the workflow traceable." },
] as const satisfies readonly (Feature & { id: string })[];

export default function Features({ activeFeature, setActiveFeature }: { activeFeature: string | null; setActiveFeature: (id: string | null) => void }) {
  const activeData = activeFeature ? (featureDetails.find((feature) => feature.id === activeFeature) ?? null) : null;

  return (
    <section id="features" className="hero-panel features-page" aria-labelledby="features-heading">
      <div className="features-content">
        <header className="page-heading">
          <p className="eyebrow">WorkBench capabilities</p>
          <h1 id="features-heading">Features</h1>
          <p>Local tools for turning sensitive documents into useful, reviewable work.</p>
        </header>
        <ul className="feature-list" aria-label="WorkBench features">
          {featureDetails.map(({ id, title }) => (
            <li key={id}>
              <FeatureCard id={id} title={title} onClick={setActiveFeature} />
            </li>
          ))}
        </ul>
      </div>
      {activeData && <FeatureModal feature={activeData} onClose={() => setActiveFeature(null)} />}
    </section>
  );
}
