export default function Footer() {
  return (
    <footer className="footer">
      <div className="container">
        <div className="footer-inner">
          <div className="footer-brand">
            <div className="footer-brand-name">WORKBENCH</div>
            <p className="footer-brand-desc">Private AI. Controlled execution. Reviewable work.</p>
          </div>
          <div className="footer-links">
            <div className="footer-col">
              <h4>Product</h4>
              <ul>
                <li><a href="#features">Features</a></li>
                <li><a href="#download">Download</a></li>
              </ul>
            </div>
            <div className="footer-col">
              <h4>Project</h4>
              <ul>
                <li><a href="#about">About</a></li>
                <li><a href="https://github.com/BrandNewDevs/WorkBench" target="_blank" rel="noreferrer">GitHub</a></li>
              </ul>
            </div>
          </div>
        </div>
        <div className="footer-bottom">
          <span>&copy; 2026 WorkBench</span>
          <span>Local-first AI workbench</span>
        </div>
      </div>
    </footer>
  );
}
