import { FaGithub } from "react-icons/fa6";
import AboutSection from "./AboutSection";

const teamMembers = [
  { initials: "JK", name: "Jiya Kumari", role: "Team Leader / Frontend Engineer", github: "jiya-22" },
  { initials: "SS", name: "Shivangi Sharma", role: "Frontend Engineer", github: "shivangiii18" },
  { initials: "YS", name: "Yajush Srivastava", role: "AI Engineer", github: "Yajush-afk" },
  { initials: "KB", name: "Kritiraj Basumatary", role: "Frontend/Design Engineer", github: "fuzzyKenny" },
  { initials: "KS", name: "Kushagra Saxena", role: "Backend Engineer", github: "Kushagra0210" },
  { initials: "AS", name: "Akshat Singh", role: "Backend Engineer", github: "AkshatSingh4477" },
];

export default function TeamGrid() {
  return (
    <AboutSection title="Meet Our Team" subtitle="The minds behind WorkBench">
      <div className="team-grid">
        {teamMembers.map((m) => {
          const profileUrl = `https://github.com/${m.github}`;
          return (
            <article key={m.initials} className="team-card">
              <div className="team-avatar">{m.initials}</div>
              <h3><a href={profileUrl} target="_blank" rel="noreferrer">{m.name}</a></h3>
              <span className="team-role">{m.role}</span>
              <a href={profileUrl} target="_blank" rel="noreferrer" className="github-link" aria-label={`${m.name} GitHub`}>
                <FaGithub size={14} aria-hidden="true" />
              </a>
            </article>
          );
        })}
      </div>
    </AboutSection>
  );
}
