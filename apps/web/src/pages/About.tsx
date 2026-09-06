import BackToHome from "../components/layout/BackToHome";
import AboutHero from "../components/about/AboutHero";
import VisionMission from "../components/about/VisionMission";
import CorePrinciples from "../components/about/CorePrinciples";
import TechStack from "../components/about/TechStack";
import ComparisonPanel from "../components/about/ComparisonPanel";
import TeamGrid from "../components/about/TeamGrid";

export default function About() {
  return (
    <section id="about" className="about-page" aria-labelledby="about-heading">
      <div className="about-content">
        <BackToHome />
        <AboutHero />
        <VisionMission />
        <CorePrinciples />
        <TeamGrid />
        <ComparisonPanel />
        <TechStack />
      </div>
    </section>
  );
}
