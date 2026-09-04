import { useState } from "react";
import {
  ArrowLeft,
  LogOut,
  Plus,
  Search,
  Server,
  Settings,
  Shield,
  UserRound,
  SlidersHorizontal,
  type LucideIcon,
} from "lucide-react";
import type { EmployeeSession } from "../../shared/contracts";
import type { ChatThread, ChatThreadId } from "../hooks/useChatThreads";
import type { SettingsSection } from "../lib/settings";
import { Button } from "./ui/button";
import { PopoverTrigger } from "./ui/popover";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "./ui/tooltip";

export type WorkspaceView = "chat" | "settings";

type WorkspaceSidebarProps = {
  access: { kind: "authenticated"; session: EmployeeSession } | { kind: "developmentBypass" };
  activeChatId: ChatThreadId;
  activeView: WorkspaceView;
  activeSettingsSection: SettingsSection;
  chats: readonly ChatThread[];
  collapsed: boolean;
  onCreateChat: () => void;
  onNavigate: (view: WorkspaceView) => void;
  onSelectChat: (threadId: ChatThreadId) => void;
  onSettingsSectionChange: (section: SettingsSection) => void;
  onSignOut?: () => void;
};

type SettingsNavigationItem = {
  section: SettingsSection;
  label: string;
  icon: LucideIcon;
};

const settingsNavigation: readonly SettingsNavigationItem[] = [
  { section: "general", label: "General", icon: SlidersHorizontal },
  { section: "localService", label: "Local service", icon: Server },
  { section: "security", label: "Security", icon: Shield },
];

function navigationClassName(active: boolean, selectedSettingsItem = false): string {
  return active
    ? `sidebar-button h-9 w-full justify-start px-3 text-foreground${selectedSettingsItem ? " sidebar-button--selected" : " bg-background"}`
    : "sidebar-button h-9 w-full justify-start px-3 text-muted-foreground";
}

export function WorkspaceSidebar({
  access,
  activeChatId,
  activeView,
  activeSettingsSection,
  chats,
  collapsed,
  onCreateChat,
  onNavigate,
  onSelectChat,
  onSettingsSectionChange,
  onSignOut,
}: WorkspaceSidebarProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const normalizedSearchQuery = searchQuery.trim().toLocaleLowerCase();
  const filteredRecentChats = chats.filter((chat) => chat.title.toLocaleLowerCase().includes(normalizedSearchQuery));

  return (
    <aside
      aria-hidden={collapsed}
      aria-label="Workspace sidebar"
      className="workspace-sidebar h-full min-w-0 overflow-hidden bg-sidebar text-foreground"
      inert={collapsed}
    >
      <div className="flex h-full min-h-0 min-w-0 flex-col" id="workspace-sidebar-content">
        <div aria-hidden="true" className="workspace-sidebar__header-clearance" />

        {activeView === "settings" ? (
          <>
            <div className="px-4 pb-2 pt-3">
              <h2 className="text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground">Settings</h2>
            </div>
            <nav aria-label="Settings navigation" className="space-y-1 px-3 pt-1">
              {settingsNavigation.map(({ section, label, icon: Icon }) => (
                <Button
                  aria-current={activeSettingsSection === section ? "page" : undefined}
                  className={navigationClassName(activeSettingsSection === section, true)}
                  key={section}
                  onClick={() => onSettingsSectionChange(section)}
                  type="button"
                  variant="ghost"
                >
                  <Icon aria-hidden="true" className="size-4" strokeWidth={1.75} />
                  <span>{label}</span>
                </Button>
              ))}
            </nav>
            <div className="min-h-0 flex-1" />
          </>
        ) : (
          <>
            <nav aria-label="Chat actions" className="workspace-sidebar__actions flex items-center gap-1 px-3 py-3">
              <div className="relative min-w-0 flex-1">
                <Label className="sr-only" htmlFor="chat-search">Search chats</Label>
                <Search
                  aria-hidden="true"
                  className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                  strokeWidth={1.75}
                />
                <Input
                  className="workspace-sidebar__search h-10 w-full rounded-lg pl-9 pr-3"
                  id="chat-search"
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="Search"
                  type="search"
                  value={searchQuery}
                />
              </div>
              <Button
                aria-label="New chat"
                className="size-8 shrink-0 px-0"
                onClick={onCreateChat}
                size="icon"
                type="button"
                variant="outline"
              >
                <Plus aria-hidden="true" className="size-4" strokeWidth={1.75} />
              </Button>
            </nav>

            <section
              aria-labelledby="recent-chats-heading"
              className="workspace-sidebar__content min-h-0 min-w-0 flex-1 overflow-y-auto px-4 pt-5"
            >
              <h2
                id="recent-chats-heading"
                className="text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground"
              >
                Recent chats
              </h2>
              {filteredRecentChats.length > 0 ? (
                <ul aria-labelledby="recent-chats-heading" className="mt-4 space-y-1">
                  {filteredRecentChats.map((chat) => (
                    <li key={chat.id}>
                      <Button
                        aria-current={activeChatId === chat.id ? "page" : undefined}
                        aria-label={`${chat.title}${chat.source === "example" ? " (Example)" : ""}`}
                        className={`sidebar-button h-9 w-full justify-start px-3 text-muted-foreground${activeChatId === chat.id ? " bg-background text-foreground" : ""}`}
                        onClick={() => onSelectChat(chat.id)}
                        type="button"
                        variant="ghost"
                      >
                        <span className="truncate">{chat.title}</span>
                        {chat.source === "example" && <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">Example</span>}
                      </Button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-4 text-xs leading-5 text-muted-foreground">
                  {normalizedSearchQuery ? "No matching chats." : "No recent chats yet."}
                </p>
              )}
            </section>
          </>
        )}

        <footer className="shrink-0 px-3 py-3">
          {activeView === "settings" ? (
            <div className="flex items-center justify-between gap-2">
              <Button
                aria-label="Back"
                className="sidebar-button h-8 shrink-0 px-2 text-muted-foreground"
                onClick={() => onNavigate("chat")}
                type="button"
                variant="ghost"
              >
                <ArrowLeft aria-hidden="true" className="size-4" strokeWidth={1.75} />
                <span>Back</span>
              </Button>
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <PopoverTrigger asChild>
                      <Button
                        aria-label="Account"
                        className="sidebar-button size-8 shrink-0 px-0 text-muted-foreground data-[state=open]:bg-background data-[state=open]:text-foreground"
                        type="button"
                        variant="ghost"
                      >
                        <UserRound aria-hidden="true" className="size-4" strokeWidth={1.75} />
                      </Button>
                    </PopoverTrigger>
                  </TooltipTrigger>
                  <TooltipContent side="right" className="px-2 py-1 text-[11px] leading-3.5">
                    Account
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </div>
          ) : (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    aria-label="Settings"
                    className="sidebar-button size-8 shrink-0 px-0 text-muted-foreground data-[state=open]:bg-background data-[state=open]:text-foreground"
                    onClick={() => onNavigate("settings")}
                    type="button"
                    variant="ghost"
                  >
                    <Settings aria-hidden="true" className="size-4" strokeWidth={1.75} />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="right" className="px-2 py-1 text-[11px] leading-3.5">
                  Settings
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}
          {access.kind === "authenticated" && onSignOut && (
            <div className="mt-3 flex items-center gap-2">
              <div className="min-w-0 flex-1 px-2">
                <p className="truncate text-xs font-medium">{access.session.user.displayName}</p>
                <p className="truncate text-[11px] text-muted-foreground">{access.session.user.employeeId}</p>
              </div>
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      aria-label="Sign out"
                      className="sidebar-button size-8 shrink-0 px-0 text-muted-foreground"
                      onClick={onSignOut}
                      type="button"
                      variant="ghost"
                    >
                      <LogOut aria-hidden="true" className="size-4" strokeWidth={1.75} />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent side="right" className="px-2 py-1 text-[11px] leading-3.5">
                    Sign out
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </div>
          )}
        </footer>
      </div>
    </aside>
  );
}
