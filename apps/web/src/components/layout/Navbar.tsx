import { useState } from "react";

export default function Navbar({ isFeaturesPage, isAboutPage }: { isFeaturesPage: boolean; isAboutPage: boolean }) {
  const [mobileOpen, setMobileOpen] = useState(false);

  const closeMobile = () => setMobileOpen(false);

  return (
    <nav aria-label="Main navigation" className="main-navigation">
      <a href="#top" className="brand-link" aria-label="WorkBench home" onClick={closeMobile}>
        <span className="brand-name">WB</span>
      </a>

      <button
        className="mobile-menu-toggle"
        aria-label={mobileOpen ? "Close menu" : "Open menu"}
        aria-expanded={mobileOpen}
        onClick={() => setMobileOpen((o) => !o)}
      >
        <span className="hamburger-line" />
        <span className="hamburger-line" />
        <span className="hamburger-line" />
      </button>

      <div className={`nav-links${mobileOpen ? " nav-links--open" : ""}`}>
        <a href="#features" aria-current={isFeaturesPage ? "page" : undefined} onClick={closeMobile}>Features</a>
        <a href="#about" aria-current={isAboutPage ? "page" : undefined} onClick={closeMobile}>About</a>
        <a href="https://github.com/BrandNewDevs/WorkBench" target="_blank" rel="noreferrer" onClick={closeMobile}>GitHub</a>
      </div>

      <a href="#download" className="availability-link" onClick={closeMobile}>Download</a>
    </nav>
  );
}
