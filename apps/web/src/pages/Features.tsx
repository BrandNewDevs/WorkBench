import FeatureCard from "../components/FeatureCard";
import FeatureModal from "../components/FeatureModal";

const featureDetails = [
  { id: "01", title: "Local AI", desc: "Run inference entirely on-device. No data leaves your machine — full privacy with local model execution." },
  { id: "02", title: "Other Features", desc: "Document tagging and organization that keeps your workflow structured and searchable." },
  { id: "03", title: "Local AI", desc: "Offline embeddings and retrieval. Your knowledge base stays on your hardware at all times." },
  { id: "04", title: "Other Features", desc: "Sandboxed code execution for safe experimentation within isolated Docker containers." },
  { id: "05", title: "Local AI", desc: "On-device OCR and vision processing. Scan and interpret documents without uploading anything." },
  { id: "06", title: "Other Features", desc: "Audit trail and session logs that record every action for compliance and transparency." },
] as const;

export default function Features({ activeFeature, setActiveFeature }: { activeFeature: string | null; setActiveFeature: (id: string | null) => void }) {
  const openFeature = (id: string) => setActiveFeature(id);
  const activeData = activeFeature ? (featureDetails.find(f => f.id === activeFeature) ?? null) : null;

  return (
    <>
      <div className="features-content">
        <div className="feature-list">
          {featureDetails.map(({ id, title }) => (
            <FeatureCard key={id} id={id} title={title} onClick={openFeature} />
          ))}
        </div>
      </div>
      <FeatureModal feature={activeData} onClose={() => setActiveFeature(null)} />
    </>
  );
}