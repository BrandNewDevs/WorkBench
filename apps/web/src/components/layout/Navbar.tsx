import { useEffect, useRef, useState } from "react";
import { Star } from "lucide-react";
import { FaGithub } from "react-icons/fa6";

export default function Navbar({ isFeaturesPage, isAboutPage }: { isFeaturesPage: boolean; isAboutPage: boolean }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [isScrolled, setIsScrolled] = useState(() => window.scrollY > 8);
  const [starOpen, setStarOpen] = useState(false);
  const [starPopped, setStarPopped] = useState(false);
  const popTimerRef = useRef<number | null>(null);
  const collapseTimerRef = useRef<number | null>(null);
  const hideStarTimerRef = useRef<number | null>(null);

  const closeMobile = () => setMobileOpen(false);

  useEffect(() => {
    const handleScroll = () => setIsScrolled(window.scrollY > 8);
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  const openStar = () => {
    if (collapseTimerRef.current !== null) {
      window.clearTimeout(collapseTimerRef.current);
      collapseTimerRef.current = null;
    }
    if (hideStarTimerRef.current !== null) {
      window.clearTimeout(hideStarTimerRef.current);
      hideStarTimerRef.current = null;
    }
    setStarOpen(true);
    if (!starPopped) {
      if (popTimerRef.current !== null) {
        window.clearTimeout(popTimerRef.current);
      }
      popTimerRef.current = window.setTimeout(() => {
        setStarPopped(true);
        popTimerRef.current = null;
      }, 160);
    }
  };

  const closeStar = () => {
    if (popTimerRef.current !== null) {
      window.clearTimeout(popTimerRef.current);
      popTimerRef.current = null;
    }
    if (collapseTimerRef.current !== null) {
      window.clearTimeout(collapseTimerRef.current);
    }
    collapseTimerRef.current = window.setTimeout(() => {
      setStarOpen(false);
      collapseTimerRef.current = null;
      hideStarTimerRef.current = window.setTimeout(() => {
        setStarPopped(false);
        hideStarTimerRef.current = null;
      }, 160);
    }, 750);
  };

  useEffect(() => () => {
    if (popTimerRef.current !== null) window.clearTimeout(popTimerRef.current);
    if (collapseTimerRef.current !== null) window.clearTimeout(collapseTimerRef.current);
    if (hideStarTimerRef.current !== null) window.clearTimeout(hideStarTimerRef.current);
  }, []);

  return (
    <nav
      aria-label="Main navigation"
      className={`main-navigation${isScrolled || mobileOpen ? " main-navigation--scrolled" : ""}`}
    >
      <div className="container navbar-inner">
        <a href="#top" className="brand-link" aria-label="WorkBench home" onClick={closeMobile}>
          <span className="brand-name">WorkBench</span>
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
        </div>

        <div className="nav-actions">
          <a
            href="https://github.com/BrandNewDevs/WorkBench"
            target="_blank"
            rel="noreferrer"
            className={`nav-github-link${starOpen ? " nav-github-link--open" : ""}${starPopped ? " nav-github-link--popped" : ""}`}
            aria-label="Star WorkBench on GitHub"
            onMouseEnter={openStar}
            onMouseLeave={closeStar}
            onFocus={openStar}
            onBlur={closeStar}
            onClick={closeMobile}
          >
            <span className="github-star-slot" aria-hidden="true">
              <Star size={16} className="github-star" />
            </span>
            <span className="github-label">Star on</span>
            <FaGithub size={16} aria-hidden="true" />
          </a>
          <a href="#download" className="availability-link" onClick={closeMobile}>Download</a>
        </div>
      </div>
    </nav>
  );
}
