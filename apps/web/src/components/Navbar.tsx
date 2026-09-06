export default function Navbar({ isFeaturesPage, isAboutPage }: { isFeaturesPage: boolean; isAboutPage: boolean }) {
  const isPage = isFeaturesPage || isAboutPage;

  return (
    <nav aria-label="Main navigation" className="main-navigation">
      <a href="#top" className="brand-link" aria-label="WorkBench home">
        <span className="brand-mark" aria-hidden="true">WB</span>
        <span className="brand-name">WORKBENCH</span>
      </a>
      <div className="nav-links">
        {isPage && <a href="#home">Home</a>}
        <a href="#features" aria-current={isFeaturesPage ? "page" : undefined}>Features</a>
        <a href="#about" aria-current={isAboutPage ? "page" : undefined}>About</a>
        <a href="https://github.com/BrandNewDevs/WorkBench" target="_blank" rel="noreferrer">GitHub</a>
      </div>
      <a href="#download" className="availability-link">Download</a>
    </nav>
  );
}
