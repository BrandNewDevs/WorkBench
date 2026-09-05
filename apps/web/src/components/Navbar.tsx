import { useState } from "react";

export default function Navbar({ isFeaturesPage }: { isFeaturesPage: boolean }) {
  return (
    <nav aria-label="Main navigation" className="main-navigation flex w-[calc(100%-2rem)] items-center gap-3 rounded-full bg-[#0c0c0c]/75 px-3 py-3 backdrop-blur-xl sm:w-[calc(100%-5rem)]">
      <a href="/" className="flex shrink-0 items-center gap-3 pl-1" aria-label="WorkBench home">
        <span className="relative grid size-10 place-items-center overflow-hidden rounded-[11px] border border-[#777] bg-[#e9e5e1] text-[#161616]" aria-hidden="true">
          <span className="absolute left-1 top-1 size-2 border-l-2 border-t-2 border-[#161616]" />
          <span className="relative text-[16px] font-black leading-none tracking-[-0.2em]">WB</span>
          <span className="absolute bottom-1 right-1 size-2 border-b-2 border-r-2 border-[#161616]" />
        </span>
        <span className="brand-name hidden text-xl font-bold text-[#f0ece8] sm:inline">WORKBENCH</span>
      </a>
      <div className="mx-auto flex items-center gap-4 px-2 text-xs text-[#aaa] sm:gap-8 sm:text-sm">
        {isFeaturesPage && <a href="/" className="transition hover:text-white">Home</a>}
        <a href="https://github.com/BrandNewDevs/WorkBench" target="_blank" rel="noreferrer" className="transition hover:text-white">Github</a>
        <a href="/features" aria-current={isFeaturesPage ? "page" : undefined} className="transition hover:text-white">Features</a>
        <a href="#about" className="transition hover:text-white">About</a>
      </div>
      <a href="#download" className="shrink-0 rounded-full bg-[#e9e5e1] px-4 py-2.5 text-sm font-medium text-[#161616] transition hover:bg-white">Download</a>
    </nav>
  );
}