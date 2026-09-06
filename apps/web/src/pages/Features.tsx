import BackToHome from "../components/layout/BackToHome";
import FeatureList from "../components/features/FeatureList";
import FlowChart from "../components/features/FlowChart";

export default function Features() {
  return (
    <section id="features" className="features-page" aria-labelledby="features-heading">
      <div className="features-content">
        <BackToHome />
        <header className="page-heading">
          <h1 id="features-heading" className="section-label">Capabilities</h1>
        </header>
        <FeatureList />
      </div>

      <section className="section" aria-labelledby="workflow-heading">
        <div className="container">
          <h2 id="workflow-heading" className="sr-only">Workflow</h2>
          <FlowChart />
        </div>
      </section>
    </section>
  );
}
