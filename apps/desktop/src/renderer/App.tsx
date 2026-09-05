import { useCallback, useEffect, useRef, useState } from "react";
import { Info } from "lucide-react";
import { LOCAL_API_ORIGIN } from "../shared/contracts";
import type { EmployeeSession } from "../shared/contracts";
import { LocalApiError, localApi } from "./api/localApi";
import type { SettingsSection } from "./lib/settings";
import { AccountPopover } from "./components/AccountPopover";
import { CommandPalette } from "./components/CommandPalette";
import { ChatPage } from "./components/ChatPage";
import { LoginScreen } from "./components/LoginScreen";
import { WindowTitleBar } from "./components/WindowTitleBar";
import { SettingsPage, type HealthState } from "./components/SettingsPage";
import { WorkspaceSidebar, type WorkspaceView } from "./components/WorkspaceSidebar";
import { showErrorToast } from "./lib/toast";
import { useChatThreads } from "./hooks/useChatThreads";
import { Toaster } from "./components/ui/sonner";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "./components/ui/tooltip";

type AuthState =
  | { kind: "restoring"; apiBaseUrl: string }
  | { kind: "revalidating"; apiBaseUrl: string; sessionId: string }
  | { kind: "signedOut"; apiBaseUrl: string; message?: string; serviceFailureKey?: string }
  | { kind: "authenticated"; apiBaseUrl: string; session: EmployeeSession; examplesEnabled: false }
  | { kind: "developmentBypass"; apiBaseUrl: string; examplesEnabled: true };

type WorkspaceAccess =
  | { kind: "authenticated"; session: EmployeeSession }
  | { kind: "developmentBypass" };

function isServiceUnavailableError(error: unknown): boolean {
  return (
    error instanceof LocalApiError &&
    (error.kind === "network" || error.kind === "timeout" || error.kind === "endpointUnavailable")
  );
}

function failureKey(error: unknown): string {
  if (error instanceof LocalApiError) {
    return `${error.kind}:${error.status ?? "none"}:${error.message}`;
  }
  if (error instanceof Error) {
    return `${error.name}:${error.message}`;
  }
  return "unknown";
}

const healthRefreshIntervalMs = 10_000;
const maxTimerDelayMs = 2_147_483_647;

function logoutFailureMessage(error: unknown): string {
  if (error instanceof LocalApiError && error.kind === "malformedJson") {
    return "Signed out locally. FastAPI returned malformed logout JSON, so server session revocation could not be confirmed.";
  }
  if (error instanceof LocalApiError && (error.kind === "network" || error.kind === "timeout")) {
    return "Signed out locally. FastAPI was unavailable, so server session revocation could not be confirmed.";
  }
  return "Signed out locally. Server session revocation failed and could not be confirmed.";
}

function Workspace({
  access,
  apiBaseUrl,
  onSignOut,
  sidebarCollapsed,
  settingsSection,
  onSettingsOpen,
  onSettingsSectionChange,
  onToggleSidebar,
  examplesEnabled,
}: {
  access: WorkspaceAccess;
  apiBaseUrl: string;
  onSignOut?: () => void;
  sidebarCollapsed: boolean;
  settingsSection: SettingsSection;
  examplesEnabled: boolean;
  onSettingsOpen: (section: SettingsSection) => void;
  onSettingsSectionChange: (section: SettingsSection) => void;
  onToggleSidebar: () => void;
}) {
  const [activeView, setActiveView] = useState<WorkspaceView>("chat");
  const [accountOpen, setAccountOpen] = useState(false);
  const [healthState, setHealthState] = useState<HealthState>({ kind: "loading" });
  const [now, setNow] = useState(() => Date.now());
  const chatThreads = useChatThreads(examplesEnabled);
  const healthRequestSequenceRef = useRef(0);
  const lastHealthFailureRef = useRef<string | undefined>(undefined);
  const handleNavigate = useCallback(
    (view: WorkspaceView) => {
      if (view === "settings") {
        onSettingsOpen("general");
      } else {
        setAccountOpen(false);
      }
      setActiveView(view);
    },
    [onSettingsOpen],
  );

  const refreshHealth = useCallback(async (showLoading = true) => {
    const requestSequence = ++healthRequestSequenceRef.current;
    if (showLoading) {
      setHealthState((current) => {
        if (current.kind === "ready") {
          return { kind: "loading", previous: { desktop: current.desktop, health: current.health } };
        }
        return current.kind === "error" && current.previous ? { kind: "loading", previous: current.previous } : current;
      });
    }
    try {
      const desktop = await window.workbench.getDesktopStatus();
      const health = await localApi.getHealth(desktop.apiBaseUrl);
      if (requestSequence !== healthRequestSequenceRef.current) {
        return;
      }
      setNow(Date.now());
      setHealthState({ kind: "ready", desktop, health });
    } catch (error) {
      if (requestSequence !== healthRequestSequenceRef.current) {
        return;
      }
      const message = error instanceof LocalApiError ? error.message : "The local service status could not be read.";
      setHealthState((current) => {
        const previous =
          current.kind === "ready"
            ? { desktop: current.desktop, health: current.health }
            : current.previous;
        return previous ? { kind: "error", message, failureKey: failureKey(error), previous } : { kind: "error", message, failureKey: failureKey(error) };
      });
    }
  }, []);

  useEffect(() => {
    if (healthState.kind === "ready") {
      lastHealthFailureRef.current = undefined;
      return;
    }
    if (healthState.kind !== "error" || lastHealthFailureRef.current === healthState.failureKey) {
      return;
    }

    lastHealthFailureRef.current = healthState.failureKey;
    showErrorToast({
      action: {
        label: "Try again",
        onClick: () => void refreshHealth(),
      },
      description: "FastAPI health check failed. Try again.",
      title: "Local service unavailable",
    });
  }, [healthState, refreshHealth]);

  useEffect(() => {
    void refreshHealth();
    const refreshTimer = window.setInterval(() => {
      setNow(Date.now());
      void refreshHealth(false);
    }, healthRefreshIntervalMs);
    return () => window.clearInterval(refreshTimer);
  }, [refreshHealth]);

  useEffect(() => {
    if (sidebarCollapsed) {
      setAccountOpen(false);
    }
  }, [sidebarCollapsed]);

  return (
    <AccountPopover access={access} onOpenChange={setAccountOpen} open={accountOpen}>
      <div
        className="workspace-shell"
        data-sidebar-collapsed={sidebarCollapsed}
      >
        <WorkspaceSidebar
        access={access}
        activeSettingsSection={settingsSection}
        activeChatId={chatThreads.activeThread.id}
        activeView={activeView}
        chats={chatThreads.threads}
        collapsed={sidebarCollapsed}
        onCreateChat={chatThreads.createChat}
        onNavigate={handleNavigate}
        onSelectChat={chatThreads.selectChat}
        onSettingsSectionChange={onSettingsSectionChange}
        onSignOut={onSignOut}
      />
        <main
          aria-label={activeView === "settings" ? "Settings workspace" : "Chat workspace"}
        className={`${activeView === "settings" ? "workspace-settings" : "workspace-chat"} relative isolate flex min-h-0 min-w-0 flex-col bg-background`}
      >
        <Toaster position="top-right" />
        <CommandPalette
          onCheckLocalService={() => {
            onSettingsOpen("localService");
            setActiveView("settings");
            void refreshHealth();
          }}
          onGoToChat={() => {
            setAccountOpen(false);
            setActiveView("chat");
          }}
          onOpenAccount={() => {
            if (sidebarCollapsed) {
              onToggleSidebar();
            }
            setActiveView("settings");
            setAccountOpen(true);
          }}
          onOpenSettings={(section) => {
            onSettingsOpen(section);
            setActiveView("settings");
          }}
          onToggleSidebar={onToggleSidebar}
        />
        {activeView === "settings" ? (
          <SettingsPage
            access={access}
            apiBaseUrl={apiBaseUrl}
            healthState={healthState}
            now={now}
            onRefreshHealth={() => void refreshHealth()}
            selectedSection={settingsSection}
          />
        ) : (
          <ChatPage
            examplesEnabled={examplesEnabled}
            key={chatThreads.activeThread.id}
            onAttachmentsChange={chatThreads.replaceAttachments}
            onDraftChange={chatThreads.updateDraft}
            onInspectionFilesChange={chatThreads.setInspectionFile}
            thread={chatThreads.activeThread}
          />
        )}
        </main>
        {access.kind === "developmentBypass" && (
          <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <div
                aria-label="Development mode. Authentication is disabled. No backend session or additional permissions are provided."
                aria-live="polite"
                className="fixed bottom-4 right-4 inline-flex cursor-help items-center gap-1.5 rounded-full border border-border bg-muted px-3 py-1.5 text-xs leading-4 text-muted-foreground shadow-sm outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                role="status"
                tabIndex={0}
              >
                <Info aria-hidden="true" className="size-3 shrink-0" strokeWidth={1.75} />
                Development mode
              </div>
            </TooltipTrigger>
            <TooltipContent
              align="end"
              collisionPadding={16}
              side="top"
              sideOffset={8}
              className="max-w-56 px-2 py-1 text-[11px] leading-3.5"
            >
              Authentication is disabled. No backend session or additional permissions are provided.
            </TooltipContent>
          </Tooltip>
          </TooltipProvider>
        )}
      </div>
    </AccountPopover>
  );
}

export function App() {
  const [authState, setAuthState] = useState<AuthState>({ kind: "restoring", apiBaseUrl: LOCAL_API_ORIGIN });
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [settingsSection, setSettingsSection] = useState<SettingsSection>("general");
  const signOutAttemptRef = useRef(0);
  const lastSessionRestoreFailureRef = useRef<string | undefined>(undefined);

  const restoreSession = useCallback(async () => {
    let apiBaseUrl = LOCAL_API_ORIGIN;
    try {
      const desktop = await window.workbench.getDesktopStatus();
      apiBaseUrl = desktop.apiBaseUrl;
      setAuthState({ kind: "restoring", apiBaseUrl });
      if (desktop.authMode === "developmentBypass") {
        setAuthState({ kind: "developmentBypass", apiBaseUrl, examplesEnabled: desktop.examplesEnabled });
        return;
      }

      const session = await localApi.restoreSession(apiBaseUrl);
      setAuthState({ kind: "authenticated", apiBaseUrl, session, examplesEnabled: desktop.examplesEnabled });
    } catch (error) {
      const message = error instanceof LocalApiError ? error.message : "The local session could not be restored.";
      const unauthorized = error instanceof LocalApiError && error.kind === "unauthorized";
      const serviceUnavailable = isServiceUnavailableError(error);
      setAuthState({
        kind: "signedOut",
        apiBaseUrl,
        message: unauthorized || serviceUnavailable ? undefined : message,
        serviceFailureKey: serviceUnavailable ? failureKey(error) : undefined,
      });
    }
  }, []);

  useEffect(() => {
    if (authState.kind === "authenticated" || authState.kind === "developmentBypass") {
      lastSessionRestoreFailureRef.current = undefined;
      return;
    }
    if (
      authState.kind !== "signedOut" ||
      authState.serviceFailureKey === undefined ||
      lastSessionRestoreFailureRef.current === authState.serviceFailureKey
    ) {
      return;
    }

    lastSessionRestoreFailureRef.current = authState.serviceFailureKey;
    showErrorToast({
      description: "Session could not be restored. Try again when FastAPI is running.",
      title: "Local service unavailable",
    });
  }, [authState]);

  useEffect(() => {
    void restoreSession();
  }, [restoreSession]);

  useEffect(() => {
    if (authState.kind !== "authenticated") {
      return;
    }

    const sessionId = authState.session.sessionId;
    const expiresAt = Date.parse(authState.session.expiresAt);
    const delay = Math.min(maxTimerDelayMs, Math.max(0, expiresAt - Date.now()));
    const expiryTimer = window.setTimeout(() => {
      setAuthState((current) => {
        if (current.kind !== "authenticated" || current.session.sessionId !== sessionId) {
          return current;
        }
        return { kind: "revalidating", apiBaseUrl: authState.apiBaseUrl, sessionId };
      });
      void localApi.restoreSession(authState.apiBaseUrl).then(
        (session) => {
          setAuthState((current) => {
            if (current.kind !== "revalidating" || current.sessionId !== sessionId) {
              return current;
            }
            return { kind: "authenticated", apiBaseUrl: authState.apiBaseUrl, session, examplesEnabled: authState.examplesEnabled };
          });
        },
        (error: unknown) => {
          setAuthState((current) => {
            if (current.kind !== "revalidating" || current.sessionId !== sessionId) {
              return current;
            }
            const expired =
              error instanceof LocalApiError &&
              (error.kind === "expiredSession" || error.kind === "unauthorized");
            const serviceUnavailable = isServiceUnavailableError(error);
            return {
              kind: "signedOut",
              apiBaseUrl: authState.apiBaseUrl,
              message: expired
                ? "Your local session has expired. Sign in again."
                : serviceUnavailable
                  ? undefined
                  : "Your local session could not be revalidated. Sign in again.",
              serviceFailureKey: serviceUnavailable ? failureKey(error) : undefined,
            };
          });
        },
      );
    }, delay);

    return () => window.clearTimeout(expiryTimer);
  }, [authState]);

  const handleAuthenticated = useCallback((session: EmployeeSession): void => {
    signOutAttemptRef.current += 1;
    setAuthState({ kind: "authenticated", apiBaseUrl: LOCAL_API_ORIGIN, session, examplesEnabled: false });
  }, []);

  const handleSignOut = useCallback((apiBaseUrl: string): void => {
    const attempt = ++signOutAttemptRef.current;
    setAuthState({
      kind: "signedOut",
      apiBaseUrl,
      message: "Signed out locally. Confirming server session revocation...",
    });

    void localApi.logout(apiBaseUrl).then(
      ({ revoked }) => {
        if (attempt !== signOutAttemptRef.current) {
          return;
        }
        setAuthState((current) =>
          current.kind === "signedOut"
            ? {
                ...current,
                message: revoked
                  ? "Signed out locally. Server session revocation succeeded."
                  : "Signed out locally. FastAPI reported that the server session was not revoked.",
              }
            : current,
        );
      },
      (error: unknown) => {
        if (attempt !== signOutAttemptRef.current) {
          return;
        }
        setAuthState((current) =>
          current.kind === "signedOut"
            ? {
                ...current,
                message: logoutFailureMessage(error),
              }
            : current,
        );
      },
    );
  }, []);

  return (
    <>
      <div className="relative h-screen overflow-hidden bg-background text-foreground">
        <WindowTitleBar
          collapsed={sidebarCollapsed}
          onToggle={() => setSidebarCollapsed((current) => !current)}
          showToggle={authState.kind === "authenticated" || authState.kind === "developmentBypass"}
        />
        {authState.kind === "restoring" || authState.kind === "revalidating" ? (
            <main className="flex h-full w-full items-center justify-center bg-background text-foreground">
              <p className="text-sm text-muted-foreground">
                {authState.kind === "revalidating"
                  ? "Revalidating your local session..."
                  : "Restoring your local session..."}
              </p>
            </main>
          ) : authState.kind === "authenticated" || authState.kind === "developmentBypass" ? (
            <Workspace
              access={
                authState.kind === "authenticated"
                  ? { kind: "authenticated", session: authState.session }
                  : { kind: "developmentBypass" }
              }
              onSignOut={
                authState.kind === "authenticated" ? () => handleSignOut(authState.apiBaseUrl) : undefined
              }
              apiBaseUrl={authState.apiBaseUrl}
              examplesEnabled={authState.examplesEnabled}
              onSettingsOpen={setSettingsSection}
              onSettingsSectionChange={setSettingsSection}
              onToggleSidebar={() => setSidebarCollapsed((current) => !current)}
              settingsSection={settingsSection}
              sidebarCollapsed={sidebarCollapsed}
            />
          ) : (
            <div className="relative h-full">
              <LoginScreen
                apiBaseUrl={authState.apiBaseUrl}
                initialMessage={authState.message}
                onAuthenticated={handleAuthenticated}
              />
            </div>
          )}
      </div>
    </>
  );
}
