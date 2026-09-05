import FeatureCard from "../components/feature-card";
import FeatureModal, { type Feature } from "../components/feature-modal";
import FlowChart from "../components/flow-chart";


const featureDetails = [
  { id: "01", title: "Local AI", category: "AI CAPABILITIES", desc: "Run inference entirely on-device. No data leaves your machine. WorkBench uses local model execution for confidential work." },
  { id: "02", title: "Document Workflow", category: "WORKFLOW", desc: "Organize inspection documents, uploads, and generated drafts in one local workspace." },
  { id: "03", title: "Local Retrieval", category: "KNOWLEDGE", desc: "Search an offline knowledge base. Retrieved source metadata keeps citations tied to the documents on your hardware." },
  { id: "04", title: "Sandboxed Execution", category: "SECURITY", desc: "Run coding tasks in isolated Docker containers with no network access and a temporary task-folder mount." },
  { id: "05", title: "OCR and Vision", category: "VISION", desc: "Process scanned pages and site photographs with local OCR and vision models, without uploading them." },
  { id: "06", title: "Approval and Audit", category: "GOVERNANCE", desc: "Review drafts before export. Session logs and approval records keep the workflow traceable." },
] as const satisfies readonly (Feature & { id: string; category: string })[];

const featureIcons: Record<string, JSX.Element> = {
  "Local AI": <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2a2 2 0 0 1 2 2c0 .74-.4 1.39-1 1.73V7h1a7 7 0 0 1 7 7h1a1 1 0 0 1 1 1v3a1 1 0 0 1-1 1h-1v1a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-1H2a1 1 0 0 1-1-1v-3a1 1 0 0 1 1-1h1a7 7 0 0 1 7-7h1V5.73c-.6-.34-1-.99-1-1.73a2 2 0 0 1 2-2z"/><circle cx="12" cy="14" r="3"/></svg>,
  "Document Workflow": <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>,
  "Local Retrieval": <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>,
  "Sandboxed Execution": <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>,
  "OCR and Vision": <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>,
  "Approval and Audit": <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>,
};

export default function Features({ activeFeature, setActiveFeature }: { activeFeature: string | null; setActiveFeature: (id: string | null) => void }) {
  const activeData = activeFeature ? (featureDetails.find((f) => f.id === activeFeature) ?? null) : null;

  return (
    <section id="features" className="features-page" aria-labelledby="features-heading">
      <div className="features-content">
        <header className="page-heading">
          <span className="section-label" style={{ justifyContent: "center" }}>Capabilities</span>
        </header>
        <ul className="feature-list" aria-label="WorkBench features">
          {featureDetails.map(({ id, title, category, desc }) => (
            <li key={id}>
              <FeatureCard
                id={id}
                title={title}
                category={category}
                desc={desc}
                icon={featureIcons[title]}
                onClick={setActiveFeature}
              />
            </li>
          ))}
        </ul>
      </div>

      {/* Workflow */}
      <section className="section" aria-labelledby="workflow-heading">
        <div className="container">
          <FlowChart />
        </div>
      </section>

      {activeData && <FeatureModal feature={activeData} onClose={() => setActiveFeature(null)} />}
    </section>
  );
}
