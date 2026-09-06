import AboutSection from "./AboutSection";

export default function VisionMission() {
  return (
    <AboutSection title="Vision & Mission">
      <div className="vision-mission-grid">
        <article className="about-card">
          <span className="about-card-label">Our Vision</span>
          <p>We envision a future where organizations can harness the power of AI without compromising data sovereignty, enabling faster decision-making while maintaining complete control over their most confidential information.</p>
        </article>
        <article className="about-card">
          <span className="about-card-label">Our Mission</span>
          <p>To deliver practical AI capabilities that operate entirely on-premise, ensuring that confidential inspection reports, SOPs, and operational data never leave the organization's controlled environment.</p>
        </article>
      </div>
    </AboutSection>
  );
}
