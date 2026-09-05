import type { ReactNode } from "react";
import type { DesktopStatus, EmployeeSession, HealthResponse } from "../../shared/contracts";
import { isHealthFresh } from "../lib/health";
import type { SettingsSection } from "../lib/settings";
import { Button } from "./ui/button";

type HealthSnapshot = { desktop: DesktopStatus; health: HealthResponse };

export type HealthState =
  | { kind: "loading"; previous?: HealthSnapshot }
  | HealthSnapshot & { kind: "ready" }
  | { kind: "error"; message: string; failureKey: string; previous?: HealthSnapshot };

export type SettingsAccess =
  | { kind: "authenticated"; session: EmployeeSession }
  | { kind: "developmentBypass" };

type SettingsPageProps = {
  access: SettingsAccess;
  apiBaseUrl: string;
  healthState: HealthState;
  now: number;
  onRefreshHealth: () => void;
  selectedSection: SettingsSection;
};

type GroupedSettingsRowProps = {
  title: string;
  description: string;
  children: ReactNode;
};

function lastKnownHealth(healthState: HealthState): HealthSnapshot | undefined {
  if (healthState.kind === "ready") return healthState;
  return healthState.previous;
}

function healthStatusLabel(healthState: HealthState, now: number): string {
  if (healthState.kind === "loading") return "Checking";
  if (healthState.kind === "error") return "Unavailable";
  if (!isHealthFresh(healthState.health, now)) return "Stale";
  return healthState.health.status === "healthy" ? "Healthy" : "Degraded";
}

function checkedAtLabel(healthState: HealthState, now: number): string {
  const snapshot = lastKnownHealth(healthState);
  if (!snapshot) return "Not available";

  const checkedAt = Date.parse(snapshot.health.checkedAt);
  if (!Number.isFinite(checkedAt)) return "Not available";

  const formatted = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(checkedAt);
  return isHealthFresh(snapshot.health, now) ? formatted : `${formatted} (stale)`;
}

function statusDot() {
  return <span aria-hidden="true" className="size-2 shrink-0 rounded-full bg-muted-foreground" />;
}

function GroupedSettingsRow({ title, description, children }: GroupedSettingsRowProps) {
  return (
    <div className="grid gap-2 px-4 py-3.5 sm:grid-cols-[minmax(0,1fr)_minmax(0,16rem)] sm:items-start sm:gap-8">
      <div className="min-w-0">
        <p className="text-sm font-medium text-foreground">{title}</p>
        <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{description}</p>
      </div>
      <div className="min-w-0 text-sm text-foreground sm:text-right">{children}</div>
    </div>
  );
}

function GroupedSettingsPanel({ children, label }: { children: ReactNode; label: string }) {
  return (
    <div aria-label={label} className="overflow-hidden rounded-lg border border-border bg-muted/30" role="group">
      <div className="divide-y divide-border">{children}</div>
    </div>
  );
}

function GeneralSection({ access, healthState }: { access: SettingsAccess; healthState: HealthState }) {
  const desktopStatus = lastKnownHealth(healthState)?.desktop;
  const serviceMode = desktopStatus
    ? desktopStatus.serviceMode === "managed" ? "Managed local service" : "Attached local service"
    : "Unknown";
  const authenticationMode = access.kind === "authenticated" ? "Authenticated local employee" : "Development bypass";

  return (
    <GroupedSettingsPanel label="General settings">
      <GroupedSettingsRow description="The name shown in the desktop client." title="Application">WorkBench</GroupedSettingsRow>
      <GroupedSettingsRow description="The local service connection used by this client." title="Service mode">{serviceMode}</GroupedSettingsRow>
      <GroupedSettingsRow description="The identity mode used for this session." title="Authentication mode">{authenticationMode}</GroupedSettingsRow>
    </GroupedSettingsPanel>
  );
}

function LocalServiceSection({ apiBaseUrl, healthState, now, onRefreshHealth }: {
  apiBaseUrl: string;
  healthState: HealthState;
  now: number;
  onRefreshHealth: () => void;
}) {
  const configuredUrl = lastKnownHealth(healthState)?.desktop.apiBaseUrl ?? apiBaseUrl;

  return (
    <>
      <GroupedSettingsPanel label="Local service settings">
        <GroupedSettingsRow description="The most recent FastAPI health check." title="Status">
          <span className="inline-flex items-center gap-2">{statusDot()}{healthStatusLabel(healthState, now)}</span>
        </GroupedSettingsRow>
        <GroupedSettingsRow description="The local API address used by this client." title="Loopback URL">
          <code className="break-all font-mono text-xs text-muted-foreground">{configuredUrl}</code>
        </GroupedSettingsRow>
        <GroupedSettingsRow description="When this client last received a health response." title="Last checked">
          <span className="flex flex-wrap items-center justify-between gap-3 sm:justify-end">
            <span>{checkedAtLabel(healthState, now)}</span>
            <Button className="h-8 shrink-0" onClick={onRefreshHealth} type="button" variant="outline">
              Check again
            </Button>
          </span>
        </GroupedSettingsRow>
      </GroupedSettingsPanel>
      {healthState.kind === "error" && <p aria-live="polite" className="mt-4 text-sm leading-5 text-muted-foreground" role="status">{healthState.message}</p>}
    </>
  );
}

function freshHealth(healthState: HealthState, now: number): HealthResponse | undefined {
  return healthState.kind === "ready" && isHealthFresh(healthState.health, now) ? healthState.health : undefined;
}

function securityValue(value: string | number | undefined): string | number {
  return value ?? "Unknown";
}

function SecuritySection({ healthState, now }: { healthState: HealthState; now: number }) {
  const snapshot = lastKnownHealth(healthState);
  const health = freshHealth(healthState, now);
  const localInference = health ? (health.localInference ? "Enabled" : "Disabled") : undefined;
  const currentModel = health?.currentModel?.trim() ? health.currentModel : undefined;
  const externalApis = health?.externalApis;
  const outboundStatus = health?.outboundStatus === "blocked" ? "Blocked" : health?.outboundStatus === "clear" ? "Clear" : undefined;

  return (
    <GroupedSettingsPanel label="Security settings">
      <GroupedSettingsRow description="Whether Electron reports its managed local service process as running." title="Desktop service">{securityValue(snapshot?.desktop.serviceRunning === true ? "Running" : snapshot?.desktop.serviceRunning === false ? "Stopped" : undefined)}</GroupedSettingsRow>
      <GroupedSettingsRow description="No verified backend air-gapped-mode status is available in this build." title="Air-gapped mode">Unknown</GroupedSettingsRow>
      <GroupedSettingsRow description="Whether the latest fresh health response reports local inference." title="Local inference">{securityValue(localInference)}</GroupedSettingsRow>
      <GroupedSettingsRow description="The model named by the latest fresh health response." title="Current model">{securityValue(currentModel)}</GroupedSettingsRow>
      <GroupedSettingsRow description="The number reported by the latest fresh health response." title="External APIs">{securityValue(externalApis)}</GroupedSettingsRow>
      <GroupedSettingsRow description="The network status reported by the latest fresh health response." title="Outbound status">{securityValue(outboundStatus)}</GroupedSettingsRow>
    </GroupedSettingsPanel>
  );
}

function settingsSectionLabel(section: SettingsSection): string {
  const labels: Record<SettingsSection, string> = {
    general: "General",
    localService: "Local service",
    security: "Security",
  };
  return labels[section];
}

export function SettingsPage({ access, apiBaseUrl, healthState, now, onRefreshHealth, selectedSection }: SettingsPageProps) {
  return (
    <section aria-labelledby="settings-heading" className="w-full px-8 pb-12 pt-16">
      <header className="pb-5">
        <div className="flex items-baseline gap-2">
          <h1 id="settings-heading" className="text-md font-medium tracking-tight text-muted-foreground">Settings</h1>
          <span aria-hidden="true" className="text-sm text-muted-foreground/60">/</span>
          <span className="text-md text-foreground">{settingsSectionLabel(selectedSection)}</span>
        </div>
      </header>
      <div className="mx-auto w-full max-w-3xl">
        {selectedSection === "general" && <GeneralSection access={access} healthState={healthState} />}
        {selectedSection === "localService" && <LocalServiceSection apiBaseUrl={apiBaseUrl} healthState={healthState} now={now} onRefreshHealth={onRefreshHealth} />}
        {selectedSection === "security" && <SecuritySection healthState={healthState} now={now} />}
      </div>
    </section>
  );
}
