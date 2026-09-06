export default function About() {
  return (
    <section id="about" className="about-page" aria-labelledby="about-heading">
      <div className="about-content">
        {/* Header */}
        <header className="page-heading">
          <p className="eyebrow">The project</p>
          <h1 id="about-heading">About WorkBench</h1>
          <p>A sovereign, local-first AI workbench for confidential industrial and government work.</p>
        </header>

        {/* Vision & Mission */}
        <div className="about-section">
          <h2 className="section-title">Vision & Mission</h2>
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
        </div>

        {/* Core Principles */}
        <div className="about-section">
          <h2 className="section-title">Core Principles</h2>
          <div className="principles-grid">
            <article className="about-card">
              <h3>Local-First Architecture</h3>
              <p>All inference, embeddings, OCR/vision, retrieval, tool execution, artifacts, logs, and session data remain on the local machine or approved organization infrastructure. No cloud AI APIs, no remote fallbacks, no telemetry.</p>
            </article>
            <article className="about-card">
              <h3>Air-Gapped Operation</h3>
              <p>Designed to work in completely isolated environments with zero external connections. The system demonstrates zero external API use and provides live network verification.</p>
            </article>
            <article className="about-card">
              <h3>Approval-Gated Workflow</h3>
              <p>Human oversight at every critical step. The AI proposes; deterministic workflow logic enforces routing eligibility, task stages, permission checks, and validation. Users approve all side effects before execution.</p>
            </article>
            <article className="about-card">
              <h3>Evidence-Based Output</h3>
              <p>Citations are application-controlled from retrieved source metadata. Generated artifacts are labeled as drafts until user approval. The system states uncertainty rather than inventing conclusions.</p>
            </article>
          </div>
        </div>

        {/* Technology Stack */}
        <div className="about-section">
          <h2 className="section-title">Technology Stack</h2>
          <div className="tech-stack">
            <div className="tech-item">
              <span className="tech-category">Client</span>
              <span className="tech-name">Electron + React + TypeScript</span>
            </div>
            <div className="tech-item">
              <span className="tech-category">Backend</span>
              <span className="tech-name">Python + FastAPI</span>
            </div>
            <div className="tech-item">
              <span className="tech-category">AI Runtime</span>
              <span className="tech-name">Ollama (Qwen3 / Qwen3-VL)</span>
            </div>
            <div className="tech-item">
              <span className="tech-category">Vector Store</span>
              <span className="tech-name">Chroma (local)</span>
            </div>
            <div className="tech-item">
              <span className="tech-category">Database</span>
              <span className="tech-name">SQLite</span>
            </div>
            <div className="tech-item">
              <span className="tech-category">Sandbox</span>
              <span className="tech-name">Docker (network-disabled)</span>
            </div>
          </div>
        </div>

        {/* Comparison: WorkBench vs Cloud Alternatives */}
        <div className="about-section">
          <h2 className="section-title">Why WorkBench?</h2>
          <p className="section-subtitle">Comparison with cloud-based AI solutions</p>
          <div className="comparison-table-wrapper">
            <table className="comparison-table">
              <thead>
                <tr>
                  <th>Feature</th>
                  <th>WorkBench (Local-First)</th>
                  <th>Cloud AI Solutions</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Data Location</td>
                  <td className="highlight-good">100% on-premise</td>
                  <td className="highlight-bad">External servers</td>
                </tr>
                <tr>
                  <td>Network Requirement</td>
                  <td className="highlight-good">Air-gapped capable</td>
                  <td className="highlight-bad">Requires internet</td>
                </tr>
                <tr>
                  <td>Data Sovereignty</td>
                  <td className="highlight-good">Complete control</td>
                  <td className="highlight-bad">Third-party dependent</td>
                </tr>
                <tr>
                  <td>Compliance</td>
                  <td className="highlight-good">Government/Industrial ready</td>
                  <td className="highlight-bad">Variable compliance</td>
                </tr>
                <tr>
                  <td>Latency</td>
                  <td className="highlight-good">Local inference</td>
                  <td className="highlight-bad">Network dependent</td>
                </tr>
                <tr>
                  <td>Cost Model</td>
                  <td className="highlight-good">One-time hardware</td>
                  <td className="highlight-bad">Recurring subscription</td>
                </tr>
                <tr>
                  <td>Citation Control</td>
                  <td className="highlight-good">Source-verified</td>
                  <td className="highlight-bad">Model-generated</td>
                </tr>
                <tr>
                  <td>Audit Trail</td>
                  <td className="highlight-good">Local, complete</td>
                  <td className="highlight-bad">Partial, external</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        {/* Meet Our Team */}
        <div className="about-section">
          <h2 className="section-title">Meet Our Team</h2>
          <p className="section-subtitle">The minds behind WorkBench</p>
          <div className="team-grid">
            <article className="team-card">
              <div className="team-avatar">JK</div>
              <h3>Jiya Kumari</h3>
              <span className="team-role">Team Leader / Frontend Engineer</span>
              <a href="https://github.com/jiya-22" target="_blank" rel="noreferrer" className="github-link" aria-label="GitHub"><svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg></a>
            </article>
            <article className="team-card">
              <div className="team-avatar">SS</div>
              <h3>Shivangi Sharma</h3>
              <span className="team-role">Frontend Engineer</span>
              <a href="https://github.com/shivangiii18" target="_blank" rel="noreferrer" className="github-link" aria-label="GitHub"><svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg></a>
            </article>
            <article className="team-card">
              <div className="team-avatar">YS</div>
              <h3>Yajush Srivastava</h3>
              <span className="team-role">AI Engineer</span>
              <a href="https://github.com/Yajush-afk" target="_blank" rel="noreferrer" className="github-link" aria-label="GitHub"><svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg></a>
            </article>
            <article className="team-card">
              <div className="team-avatar">KB</div>
              <h3>Kritiraj Basumatary</h3>
              <span className="team-role">Frontend/Design Engineer</span>
              <a href="https://github.com/fuzzyKenny" target="_blank" rel="noreferrer" className="github-link" aria-label="GitHub"><svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg></a>
            </article>
            <article className="team-card">
              <div className="team-avatar">KS</div>
              <h3>Kushagra Saxena</h3>
              <span className="team-role">Backend Engineer</span>
              <a href="https://github.com/Kushagra0210" target="_blank" rel="noreferrer" className="github-link" aria-label="GitHub"><svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg></a>
            </article>
            <article className="team-card">
              <div className="team-avatar">AS</div>
              <h3>Akshat Singh</h3>
              <span className="team-role">Backend Engineer</span>
              <a href="https://github.com/AkshatSingh4477" target="_blank" rel="noreferrer" className="github-link" aria-label="GitHub"><svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg></a>
            </article>
          </div>
        </div>

        {/* Key Outcomes */}
        <div className="about-section">
          <h2 className="section-title">Key Outcomes</h2>
          <div className="outcomes-grid">
            <article className="outcome-card">
              <span className="outcome-number">0</span>
              <span className="outcome-label">External API Calls</span>
              <p>Complete data sovereignty with zero cloud dependencies</p>
            </article>
            <article className="outcome-card">
              <span className="outcome-number">100%</span>
              <span className="outcome-label">On-Premise</span>
              <p>All processing happens on your local workstation</p>
            </article>
            <article className="outcome-card">
              <span className="outcome-number">1</span>
              <span className="outcome-label">Workstation</span>
              <p>Single-machine deployment for the entire stack</p>
            </article>
            <article className="outcome-card">
              <span className="outcome-number">&lt;2s</span>
              <span className="outcome-label">Local Inference</span>
              <p>Fast response times with local model execution</p>
            </article>
          </div>
        </div>
      </div>
    </section>
  );
}