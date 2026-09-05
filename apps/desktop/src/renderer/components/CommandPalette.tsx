import { lazy, Suspense, useEffect, useState } from "react";
import type { SettingsSection } from "../lib/settings";

const CommandPaletteDialog = lazy(() =>
  import("./CommandPaletteDialog").then(({ CommandPaletteDialog: Dialog }) => ({ default: Dialog })),
);

type CommandPaletteProps = {
  onCheckLocalService: () => void;
  onGoToChat: () => void;
  onOpenAccount: () => void;
  onOpenSettings: (section: SettingsSection) => void;
  onToggleSidebar: () => void;
};

export function CommandPalette({ onCheckLocalService, onGoToChat, onOpenAccount, onOpenSettings, onToggleSidebar }: CommandPaletteProps) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.isComposing || event.key.toLocaleLowerCase() !== "k" || (!event.metaKey && !event.ctrlKey) || event.altKey) {
        return;
      }

      event.preventDefault();
      setOpen((current) => !current);
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);

  return (
    <Suspense fallback={null}>
      {open ? (
        <CommandPaletteDialog
          onCheckLocalService={onCheckLocalService}
          onGoToChat={onGoToChat}
          onOpenAccount={onOpenAccount}
          onOpenChange={setOpen}
          onOpenSettings={onOpenSettings}
          onToggleSidebar={onToggleSidebar}
          open={open}
        />
      ) : null}
    </Suspense>
  );
}
