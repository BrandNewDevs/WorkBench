import {
  ArrowUp,
  ChevronDown,
  MessageSquare,
  PanelLeftClose,
  Paperclip,
  Plus,
  Search,
  ShieldCheck,
} from "lucide-react";

const recentChats = [
  "Inspection report review",
  "Pump maintenance notes",
  "Draft approval memo",
];

export default function AppMockup() {
  return (
    <div
      className="app-mockup"
      role="img"
      aria-label="Animated WorkBench chat preview showing a local model answering questions about an inspection report."
    >
      <div className="app-mockup-bar">
        <div className="app-mockup-window-controls">
          <span className="app-mockup-dot" />
          <span className="app-mockup-dot" />
          <span className="app-mockup-dot" />
        </div>
        <span className="app-mockup-product">WorkBench</span>
        <span className="app-mockup-local">
          <ShieldCheck size={12} />
          Local only
        </span>
      </div>

      <div className="app-mockup-body">
        <aside className="app-mockup-sidebar">
          <div className="mockup-sidebar-heading">
            <span>Chats</span>
            <PanelLeftClose size={14} />
          </div>

          <div className="mockup-sidebar-search">
            <Search size={13} />
            <span>Search</span>
          </div>

          <div className="mockup-new-chat">
            <Plus size={13} />
            <span>New chat</span>
          </div>

          <span className="mockup-sidebar-label">Recent</span>
          <div className="mockup-chat-list">
            {recentChats.map((chat, index) => (
              <div key={chat} className={`mockup-chat-item${index === 0 ? " active" : ""}`}>
                <MessageSquare size={12} />
                <span>{chat}</span>
              </div>
            ))}
          </div>

          <div className="mockup-sidebar-footer">
            <span className="mockup-local-dot" />
            Ollama connected
          </div>
        </aside>

        <div className="app-mockup-main">
          <div className="mockup-chat-thread">
            <div className="mockup-message mockup-message-user">
              Review the inspection report.
            </div>

            <div className="mockup-assistant-stage">
              <div className="mockup-thinking mockup-thinking-1">
                <div className="mockup-assistant-mark">W</div>
                <span /><span /><span />
              </div>

              <div className="mockup-message mockup-message-assistant">
                <div className="mockup-assistant-mark">W</div>
                <div className="mockup-assistant-content">
                  <span className="mockup-assistant-name">WorkBench</span>
                  <p>I found three items that need review. The highest priority is a pressure reading outside the approved range.</p>
                  <span className="mockup-citation">SOP-14 · page 7</span>
                </div>
              </div>
            </div>

            <div className="mockup-message mockup-message-user mockup-message-user-2">
              Draft the corrective action.
            </div>

            <div className="mockup-assistant-stage">
              <div className="mockup-thinking mockup-thinking-2">
                <div className="mockup-assistant-mark">W</div>
                <span /><span /><span />
              </div>

              <div className="mockup-message mockup-message-assistant mockup-message-assistant-2">
                <div className="mockup-assistant-mark">W</div>
                <div className="mockup-assistant-content">
                  <span className="mockup-assistant-name">WorkBench</span>
                  <p>Draft ready: recalibrate sensor P-112, replace the fitting, and re-log the reading for sign-off.</p>
                  <span className="mockup-citation">SOP-14 · page 9</span>
                </div>
              </div>
            </div>
          </div>

          <div className="mockup-composer">
            <span className="mockup-composer-copy">
              <span className="mockup-composer-placeholder">Ask about your local files</span>
              <span className="mockup-composer-typed">
                <span className="mockup-typed-text">Review the inspection report.</span>
              </span>
              <span className="mockup-composer-typed mockup-composer-typed-b">
                <span className="mockup-typed-text">Draft the corrective action.</span>
              </span>
            </span>
            <div className="mockup-composer-toolbar">
              <span className="mockup-composer-model">
                Qwen3 4B
                <ChevronDown size={11} />
              </span>
              <span className="mockup-composer-action"><Paperclip size={13} /></span>
              <span className="mockup-send"><ArrowUp size={13} /></span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
