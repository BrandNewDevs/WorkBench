import BackToHome from "../components/layout/BackToHome";
import FeatureList from "../components/features/FeatureList";
import FlowChart from "../components/features/FlowChart";
import WhyWorkbench from "../components/features/WhyWorkbench";

export default function Features() {
  return (
    <section id="features" className="features-page" aria-labelledby="features-heading">
      <div className="features-content">
        <BackToHome />
        <header className="page-heading">
          <h1 id="features-heading">Capabilities</h1>
        </header>
        <FeatureList />
      </div>

      <section className="section flow-section" aria-labelledby="workflow-heading">
        <div className="container">
          <h2 id="workflow-heading" className="sr-only">Workflow</h2>
          <FlowChart />
        </div>
      </section>

      <section className="section why-merge" aria-labelledby="why-heading">
        <div className="container">
          <h2 id="why-heading" className="sr-only">Why WorkBench?</h2>
          <WhyWorkbench />
        </div>
      </section>
    </section>
  );
}
