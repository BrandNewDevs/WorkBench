import BackToHome from "../components/layout/BackToHome";
import AboutHero from "../components/about/AboutHero";
import VisionMission from "../components/about/VisionMission";
import CorePrinciples from "../components/about/CorePrinciples";
import Team from "../components/about/Team";

export default function About() {
  return (
    <section id="about" className="about-page" aria-labelledby="about-heading">
      <div className="about-content">
        <BackToHome />
        <AboutHero />
        <VisionMission />
        <CorePrinciples />
        <Team />
      </div>
    </section>
  );
}
