import BackToHome from "../components/layout/BackToHome";
import VisionMission from "../components/about/VisionMission";
import CorePrinciples from "../components/about/CorePrinciples";
import TechStack from "../components/about/TechStack";
import ComparisonTable from "../components/about/ComparisonTable";
import TeamGrid from "../components/about/TeamGrid";
import KeyOutcomes from "../components/about/KeyOutcomes";

export default function About() {
  return (
    <section id="about" className="about-page" aria-labelledby="about-heading">
      <div className="about-content">
        <BackToHome />
        <header className="page-heading">
          <h1 id="about-heading">About WorkBench</h1>
          <p>A sovereign, local-first AI workbench for confidential industrial and government work.</p>
        </header>

        <VisionMission />
        <CorePrinciples />
        <TechStack />
        <ComparisonTable />
        <TeamGrid />
        <KeyOutcomes />
      </div>
    </section>
  );
}
