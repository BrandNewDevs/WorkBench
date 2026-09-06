export default function Navbar({ isFeaturesPage, isAboutPage }: { isFeaturesPage: boolean; isAboutPage: boolean }) {
  const isPage = isFeaturesPage || isAboutPage;

  return (
    <nav aria-label="Main navigation" className="main-navigation flex w-auto max-w-[600px] items-center gap-3 rounded-full bg-[#0c0c0c]/75 px-3 py-3 backdrop-blur-xl">
      <a href="#top" className="brand-link flex shrink-0 items-center gap-3 pl-1" aria-label="WorkBench home">
        <span className="brand-mark relative grid size-10 place-items-center overflow-hidden rounded-[11px] border border-[#777] bg-[#e9e5e1] text-[#161616]" aria-hidden="true">
          <span className="absolute left-1 top-1 size-2 border-l-2 border-t-2 border-[#161616]" />
          <span className="relative text-[16px] font-black leading-none tracking-[-0.2em]">WB</span>
          <span className="absolute bottom-1 right-1 size-2 border-b-2 border-r-2 border-[#161616]" />
        </span>
        <span className="brand-name hidden text-xl font-bold text-[#f0ece8] md:inline">WORKBENCH</span>
      </a>
      <div className="nav-links mx-auto flex items-center gap-4 px-2 text-xs text-[#aaa] md:gap-8 md:text-sm">
        {isPage && <a href="#home" className="transition hover:text-white">Home</a>}
        <a href="https://github.com/BrandNewDevs/WorkBench" target="_blank" rel="noreferrer" className="transition hover:text-white">GitHub</a>
        <a href="#features" aria-current={isFeaturesPage ? "page" : undefined} className="transition hover:text-white">Features</a>
        <a href="#about" aria-current={isAboutPage ? "page" : undefined} className="transition hover:text-white">About</a>
      </div>
      <a href="#download" className="availability-link shrink-0 rounded-full bg-[#e9e5e1] px-4 py-2.5 text-sm font-medium text-[#161616] transition hover:bg-white">Availability</a>
    </nav>
  );
}
