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
    <AboutSection title="Meet Our Team">
      <div className="team-grid">
        {teamMembers.map((m) => {
          const profileUrl = `https://github.com/${m.github}`;
          return (
            <article key={m.initials} className="team-card">
              <div className="team-avatar">{m.initials}</div>
              <h3>{m.name}</h3>
              <span className="team-role">{m.role}</span>
              <a href={profileUrl} target="_blank" rel="noreferrer" className="github-link" aria-label={`${m.name} GitHub`}>
                <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>
              </a>
            </article>
          );
        })}
      </div>
    </AboutSection>
  );
}
