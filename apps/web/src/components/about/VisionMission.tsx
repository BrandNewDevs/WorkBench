import { Eye, Target } from "lucide-react";
import AboutSection from "./AboutSection";

export default function VisionMission() {
  return (
    <AboutSection title="Vision & Mission">
      <div className="about-two-col">
        <article className="about-text-block">
          <div className="about-text-header">
            <Eye size={18} className="about-text-icon" aria-hidden="true" />
            <span className="about-text-label">Our Vision</span>
          </div>
          <p>We envision a future where organizations can harness the power of AI without compromising data sovereignty, enabling faster decision-making while maintaining complete control over their most confidential information.</p>
        </article>
        <article className="about-text-block">
          <div className="about-text-header">
            <Target size={18} className="about-text-icon" aria-hidden="true" />
            <span className="about-text-label">Our Mission</span>
          </div>
          <p>To deliver practical AI capabilities that operate entirely on-premise, ensuring that confidential inspection reports, SOPs, and operational data never leave the organization's controlled environment.</p>
        </article>
      </div>
    </AboutSection>
  );
}
