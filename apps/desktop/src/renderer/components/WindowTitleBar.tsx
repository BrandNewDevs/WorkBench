import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { Button } from "./ui/button";

type WindowTitleBarProps = {
  collapsed: boolean;
  onToggle: () => void;
  showToggle?: boolean;
};

function isMacPlatform(): boolean {
  return /mac/i.test(navigator.platform);
}

export function WindowTitleBar({ collapsed, onToggle, showToggle = true }: WindowTitleBarProps) {
  return (
    <header
      aria-label="Window title bar"
      className={`window-titlebar ${isMacPlatform() ? "window-titlebar--mac" : "window-titlebar--system-controls-right"}`}
    >
      <div className="window-titlebar__content">
        {showToggle && (
          <>
            <Button
              aria-controls="workspace-sidebar-content"
              aria-expanded={!collapsed}
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              className="window-titlebar__toggle size-9 shrink-0 px-0"
              data-state={collapsed ? "closed" : "open"}
              onClick={onToggle}
              title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              variant="ghost"
            >
              {collapsed ? (
                <PanelLeftOpen aria-hidden="true" className="size-5" strokeWidth={1.75} />
              ) : (
                <PanelLeftClose aria-hidden="true" className="size-5" strokeWidth={1.75} />
              )}
            </Button>
          </>
        )}
      </div>
    </header>
  );
}
