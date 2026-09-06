import { useState, useCallback } from "react";

export default function Navbar({ isFeaturesPage, isAboutPage }: { isFeaturesPage: boolean; isAboutPage: boolean }) {
  const [mobileOpen, setMobileOpen] = useState(false);

  const closeMobile = useCallback(() => setMobileOpen(false), []);

  return (
    <nav aria-label="Main navigation" className="main-navigation">
      <a href="#top" className="brand-link" aria-label="WorkBench home" onClick={closeMobile}>
        <svg className="brand-icon" width="28" height="28" viewBox="0 0 28 28" fill="none" xmlns="http://www.w3.org/2000/svg">
          <rect width="28" height="28" rx="6" fill="url(#grad)"/>
          <path d="M7 10h14v2H7zM7 14h10v2H7zM7 18h14v2H7z" fill="white" opacity="0.9"/>
          <circle cx="21" cy="15" r="3" fill="white" opacity="0.5"/>
          <defs>
            <linearGradient id="grad" x1="0" y1="0" x2="28" y2="28">
              <stop offset="0%" stopColor="#7C3AED"/>
              <stop offset="100%" stopColor="#4F46E5"/>
            </linearGradient>
          </defs>
        </svg>
        <span className="brand-name">WORKBENCH</span>
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
