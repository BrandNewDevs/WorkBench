import { Search } from "lucide-react";

const sidebarItems = ["Documents", "Knowledge Base", "Drafts", "Audit Log", "Settings"];
const documentCards = ["Document", "Retrieval"];

export default function AppMockup() {
  return (
    <div className="app-mockup" aria-hidden="true">
      <div className="app-mockup-bar">
        <span className="app-mockup-dot" />
        <span className="app-mockup-dot" />
        <span className="app-mockup-dot" />
        <span className="app-mockup-url">workbench://local/workspace</span>
      </div>
      <div className="app-mockup-body">
        <div className="app-mockup-sidebar">
          {sidebarItems.map((item, i) => (
            <div key={item} className={`app-mockup-sidebar-item${i === 0 ? " active" : ""}`}>
              <span className="app-mockup-sidebar-icon" />
              {item}
            </div>
          ))}
        </div>
        <div className="app-mockup-main">
          <div className="mockup-header">
            <span className="mockup-title">Workspace</span>
            <span className="mockup-status">
              <span className="mockup-status-dot" />
              Local model active
            </span>
          </div>
          <div className="mockup-search">
            <Search size={12} aria-hidden="true" />
            Search documents...
            <span className="mockup-search-bar" />
          </div>
          <div className="mockup-cards">
            {documentCards.map((label) => (
              <div key={label} className="mockup-card">
                <div className="mockup-card-label">{label}</div>
                <div className="mockup-card-lines">
                  <span className="mockup-line" />
                  <span className="mockup-line" />
                  <span className="mockup-line" />
                </div>
              </div>
            ))}
            <div className="mockup-ai">
              <div className="mockup-ai-header">
                <span className="mockup-ai-dot" />
                <span className="mockup-ai-label">AI Assistant</span>
              </div>
              <div className="mockup-ai-text">
                Analyzing document "Inspection_Report_042.pdf" — extracted 14 findings. Cross-referencing with local knowledge base. Draft response ready for review.
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
