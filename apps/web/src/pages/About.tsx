export default function About() {
  return (
    <section id="about" className="hero-panel features-page" aria-labelledby="about-heading">
      <div className="features-content">
        <header className="page-heading">
          <p className="eyebrow">The project</p>
          <h1 id="about-heading">About WorkBench</h1>
          <p>A local-first workbench for teams that cannot send confidential work to the cloud.</p>
        </header>
        <ul className="feature-list about-list" aria-label="About WorkBench">
          <li>
            <article className="feature-card about-card">
              <span>Our mission</span>
              <h2>Useful AI without the data leak.</h2>
            </article>
          </li>
          <li>
            <article className="feature-card about-card">
              <span>Our approach</span>
              <h2>Built locally</h2>
            </article>
          </li>
          <li>
            <article className="feature-card about-card">
              <span>Our priority</span>
              <h2>Air-gapped first</h2>
            </article>
          </li>
        </ul>
      </div>
    </section>
  );
}
