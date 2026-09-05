import { Command } from "cmdk";
import {
  MessageSquare,
  PanelLeftClose,
  RefreshCw,
  Server,
  Shield,
  SlidersHorizontal,
  UserRound,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useState } from "react";
import type { SettingsSection } from "../lib/settings";

type CommandPaletteDialogProps = {
  onCheckLocalService: () => void;
  onGoToChat: () => void;
  onOpenAccount: () => void;
  onOpenChange: (open: boolean) => void;
  onOpenSettings: (section: SettingsSection) => void;
  onToggleSidebar: () => void;
  open: boolean;
};

type PaletteCommand = {
  icon: LucideIcon;
  keywords?: string[];
  label: string;
  onSelect: () => void;
  value: string;
};

function isMacPlatform(): boolean {
  return /mac/i.test(navigator.platform);
}

function ShortcutHint() {
  return (
    <kbd aria-label={isMacPlatform() ? "Command K" : "Control K"} className="command-palette__shortcut">
      {isMacPlatform() ? "⌘" : "Ctrl"} K
    </kbd>
  );
}

export function CommandPaletteDialog({
  onCheckLocalService,
  onGoToChat,
  onOpenAccount,
  onOpenChange,
  onOpenSettings,
  onToggleSidebar,
  open,
}: CommandPaletteDialogProps) {
  const [search, setSearch] = useState("");

  const close = useCallback(() => {
    onOpenChange(false);
    setSearch("");
  }, [onOpenChange]);

  const commands: readonly PaletteCommand[] = [
    {
      icon: MessageSquare,
      label: "Go to Chat",
      onSelect: onGoToChat,
      value: "go-to-chat",
    },
    {
      icon: SlidersHorizontal,
      label: "Open General settings",
      onSelect: () => onOpenSettings("general"),
      value: "open-general-settings",
    },
    {
      icon: Server,
      label: "Open Local service settings",
      onSelect: () => onOpenSettings("localService"),
      value: "open-local-service-settings",
    },
    {
      icon: UserRound,
      label: "Open account",
      onSelect: onOpenAccount,
      value: "open-account",
    },
    {
      icon: Shield,
      label: "Open Security settings",
      onSelect: () => onOpenSettings("security"),
      value: "open-security-settings",
    },
    {
      icon: PanelLeftClose,
      label: "Toggle sidebar",
      onSelect: onToggleSidebar,
      value: "toggle-sidebar",
    },
    {
      icon: RefreshCw,
      keywords: ["health", "FastAPI", "status"],
      label: "Check local service health",
      onSelect: onCheckLocalService,
      value: "check-local-service-health",
    },
  ];

  return (
    <Command.Dialog
      contentClassName="command-palette"
      label="Command palette"
      onOpenChange={(nextOpen) => {
        onOpenChange(nextOpen);
        if (!nextOpen) setSearch("");
      }}
      open={open}
      overlayClassName="command-palette__overlay"
      vimBindings={false}
    >
      <div className="command-palette__input-row">
        <Command.Input autoFocus onValueChange={setSearch} placeholder="Search commands" value={search} />
        <ShortcutHint />
      </div>
      <Command.List className="command-palette__list">
        <Command.Empty className="command-palette__empty">No matching commands.</Command.Empty>
        <Command.Group heading="Commands">
          {commands.map(({ icon: Icon, keywords, label, onSelect, value }) => (
            <Command.Item
              className="command-palette__item"
              key={value}
              keywords={keywords}
              onSelect={() => {
                close();
                onSelect();
              }}
              value={value}
            >
              <Icon aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" strokeWidth={1.75} />
              <span>{label}</span>
            </Command.Item>
          ))}
        </Command.Group>
      </Command.List>
    </Command.Dialog>
  );
}
