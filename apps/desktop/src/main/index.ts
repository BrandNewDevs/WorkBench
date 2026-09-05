import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createServer, type Server } from "node:http";
import { createHmac, randomBytes, randomInt, timingSafeEqual } from "node:crypto";
import { app, BrowserWindow, dialog, session, type IpcMainInvokeEvent, type Session } from "electron";
import { dirname, extname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { registerDesktopIpc } from "./ipc";
import type { DesktopStatus, LocalServiceRequest, LocalServiceResponse } from "../shared/contracts";

const mainDirectory = dirname(fileURLToPath(import.meta.url));
const rendererDirectory = resolve(join(mainDirectory, "../../renderer"));
const rendererEntryPath = join(rendererDirectory, "index.html");
const loopbackHost = "127.0.0.1";
const builtRendererOrigin = "http://127.0.0.1:5173";
const useDevelopmentRenderer = process.env.WORKBENCH_DEV_RENDERER === "1";
// Keep this decision in Electron main. The renderer can only receive the resulting, typed mode over trusted IPC.
const useDevelopmentAuthBypass =
  !app.isPackaged && useDevelopmentRenderer && process.env.WORKBENCH_SKIP_AUTH === "1";
const configuredRendererUrl = process.env.VITE_DEV_SERVER_URL ?? "http://127.0.0.1:5173";
const localServiceRestartDelayMs = 1_000;
const localServiceStartAttempts = 3;
let managedServiceSession: Session | undefined;
let mainWindow: BrowserWindow | undefined;
let localService: ChildProcess | undefined;
let localServiceRestartTimer: ReturnType<typeof setTimeout> | undefined;
let localServiceFailed = false;
let stoppingLocalService = false;
let builtRendererServer: Server | undefined;
let localSigningSecret: string | undefined;
let localServicePort: number | undefined;
let localServiceCapability: string | undefined;
let localServiceVerified = false;
let startingLocalService = false;

function isLoopbackUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "http:" && (url.hostname === "127.0.0.1" || url.hostname === "localhost");
  } catch {
    return false;
  }
}

const developmentRendererUrl = isLoopbackUrl(configuredRendererUrl)
  ? configuredRendererUrl
  : "http://127.0.0.1:5173";

function rendererOrigin(): string {
  return useDevelopmentRenderer ? new URL(developmentRendererUrl).origin : builtRendererOrigin;
}

function isAllowedRendererUrl(value: string): boolean {
  try {
    return new URL(value).origin === rendererOrigin();
  } catch {
    return false;
  }
}

function isAllowedRequestUrl(value: string): boolean {
  try {
    return new URL(value).origin === rendererOrigin();
  } catch {
    return false;
  }
}

function getManagedServiceSession(): Session {
  managedServiceSession ??= session.fromPartition("workbench-managed-service");
  return managedServiceSession;
}

function isAllowedManagedServiceRequest(value: string): boolean {
  if (localServicePort === undefined) return false;
  try {
    const url = new URL(value);
    if (url.origin !== managedServiceUrl() || url.username || url.password || url.search || url.hash) {
      return false;
    }
    if (!localServiceVerified) return url.pathname === "/internal/ready";
    return ["/health", "/auth/login", "/auth/session", "/auth/logout"].includes(url.pathname);
  } catch {
    return false;
  }
}

function contentType(path: string): string {
  const types: Record<string, string> = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
  };
  return types[extname(path)] ?? "application/octet-stream";
}

async function startBuiltRendererServer(): Promise<void> {
  if (useDevelopmentRenderer || builtRendererServer) {
    return;
  }
  const origin = new URL(builtRendererOrigin);
  const server = createServer(async (request, response) => {
    if (request.method !== "GET" && request.method !== "HEAD") {
      response.writeHead(405, { Allow: "GET, HEAD" }).end();
      return;
    }
    try {
      const requestPath = decodeURIComponent(new URL(request.url ?? "/", builtRendererOrigin).pathname);
      const relativePath = requestPath === "/" ? "index.html" : requestPath.slice(1);
      const filePath = resolve(rendererDirectory, relativePath);
      if (!filePath.startsWith(`${rendererDirectory}${sep}`) && filePath !== rendererEntryPath) {
        response.writeHead(404).end();
        return;
      }
      const content = await readFile(filePath);
      response.writeHead(200, { "Content-Type": contentType(filePath), "Cache-Control": "no-store" });
      response.end(request.method === "HEAD" ? undefined : content);
    } catch {
      response.writeHead(404).end();
    }
  });
  try {
    await new Promise<void>((resolveServer, rejectServer) => {
      server.once("error", rejectServer);
      server.listen(Number(origin.port), origin.hostname, () => {
        server.off("error", rejectServer);
        resolveServer();
      });
    });
    builtRendererServer = server;
  } catch (error) {
    server.close();
    throw error;
  }
}

function stopBuiltRendererServer(): void {
  builtRendererServer?.close();
  builtRendererServer = undefined;
}

function isSigningSecret(value: string): boolean {
  return /^[a-f0-9]{96}$/i.test(value);
}

async function readSigningSecret(secretPath: string): Promise<string> {
  const secret = (await readFile(secretPath, "utf8")).trim();
  if (!isSigningSecret(secret)) {
    throw new Error("The local authentication signing secret is invalid.");
  }
  return secret;
}

async function provisionSigningSecret(): Promise<string> {
  const secretPath = join(app.getPath("userData"), "auth-signing-secret");
  try {
    return await readSigningSecret(secretPath);
  } catch (error: unknown) {
    if (!(error instanceof Error) || !("code" in error) || error.code !== "ENOENT") {
      throw error;
    }
  }
  const secret = randomBytes(48).toString("hex");
  await mkdir(app.getPath("userData"), { recursive: true });
  try {
    await writeFile(secretPath, `${secret}\n`, { encoding: "utf8", mode: 0o600, flag: "wx" });
    return secret;
  } catch (error: unknown) {
    if (!(error instanceof Error) || !("code" in error) || error.code !== "EEXIST") {
      throw error;
    }
    return readSigningSecret(secretPath);
  }
}

function managesLocalService(): boolean {
  // The renderer never attaches to an independently selected local port.
  return true;
}

function scheduleLocalServiceRestart(): void {
  if (
    !managesLocalService() ||
    stoppingLocalService ||
    startingLocalService ||
    localService ||
    localServiceRestartTimer
  ) {
    return;
  }

  localServiceRestartTimer = setTimeout(() => {
    localServiceRestartTimer = undefined;
    void startLocalService().catch(() => {
      localServiceFailed = true;
      scheduleLocalServiceRestart();
    });
  }, localServiceRestartDelayMs);
}

function localPythonExecutable(aiDirectory: string): string {
  const configuredPython = process.env.WORKBENCH_PYTHON?.trim();
  if (configuredPython) {
    return configuredPython;
  }

  const virtualEnvironmentPython = join(
    aiDirectory,
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  );
  if (existsSync(virtualEnvironmentPython)) {
    return virtualEnvironmentPython;
  }
  return process.platform === "win32" ? "python.exe" : "python3";
}

interface LocalServiceLaunch {
  executable: string;
  arguments: string[];
  workingDirectory: string;
}

function localServiceLaunch(): LocalServiceLaunch {
  if (app.isPackaged) {
    const serviceDirectory = join(process.resourcesPath, "service", "workbench-service");
    const executable = join(
      serviceDirectory,
      process.platform === "win32" ? "workbench-service.exe" : "workbench-service",
    );
    if (!existsSync(executable)) {
      throw new Error("The packaged local service is missing.");
    }
    return { executable, arguments: [], workingDirectory: serviceDirectory };
  }

  const aiDirectory = resolve(app.getAppPath(), "../ai");
  return {
    executable: localPythonExecutable(aiDirectory),
    arguments: ["-m", "uvicorn", "app.main:app", "--host", loopbackHost, "--port", String(localServicePort)],
    workingDirectory: aiDirectory,
  };
}

function allocateLocalServicePort(): number {
  return randomInt(49_152, 65_536);
}

function managedServiceUrl(): string {
  if (localServicePort === undefined) throw new Error("The managed local service has no port.");
  return `http://${loopbackHost}:${localServicePort}`;
}

async function verifyLocalService(child: ChildProcess): Promise<void> {
  const capability = localServiceCapability;
  if (!capability) throw new Error("The managed local service has no capability.");
  const nonce = randomBytes(32).toString("hex");
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (localService !== child) throw new Error("The managed local service stopped during startup.");
    try {
      const response = await getManagedServiceSession().fetch(`${managedServiceUrl()}/internal/ready`, {
        credentials: "omit",
        headers: { "X-Workbench-Readiness-Nonce": nonce },
      });
      const body = await response.json() as { proof?: unknown };
      const expected = createHmac("sha256", capability).update(nonce).digest("hex");
      if (typeof body.proof === "string" && body.proof.length === expected.length && timingSafeEqual(Buffer.from(body.proof), Buffer.from(expected))) {
        localServiceVerified = true;
        return;
      }
    } catch { /* The service may still be binding its private launch port. */ }
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 100));
  }
  throw new Error("The managed local service did not prove its launch capability.");
}

export async function startLocalService(signingSecret?: string): Promise<void> {
  localSigningSecret = signingSecret ?? localSigningSecret;
  if (!managesLocalService() || localService || localServiceRestartTimer || startingLocalService) return;

  startingLocalService = true;
  stoppingLocalService = false;
  localServiceFailed = false;
  try {
    await getManagedServiceSession().clearStorageData({ storages: ["cookies"] });
    let lastError: unknown;
    for (let attempt = 0; attempt < localServiceStartAttempts; attempt += 1) {
      localServicePort = allocateLocalServicePort();
      localServiceCapability = randomBytes(32).toString("base64url");
      localServiceVerified = false;
      const launch = localServiceLaunch();
      const child = spawn(launch.executable, launch.arguments, {
        cwd: launch.workingDirectory,
        shell: false,
        windowsHide: true,
        stdio: "ignore",
        env: {
          ...process.env,
          WORKBENCH_LOCAL_ONLY: "1",
          WORKBENCH_APP_AUTH_SIGNING_SECRET:
            localSigningSecret ?? process.env.WORKBENCH_APP_AUTH_SIGNING_SECRET,
          WORKBENCH_APP_CORS_ALLOWED_ORIGINS: JSON.stringify([rendererOrigin()]),
          WORKBENCH_APP_PORT: String(localServicePort),
          WORKBENCH_APP_LOCAL_SERVICE_CAPABILITY: localServiceCapability,
          WORKBENCH_APP_DATABASE_PATH: join(app.getPath("userData"), "workbench.db"),
        },
      });
      localService = child;
      child.once("error", () => {
        if (localService !== child) return;
        localService = undefined;
        localServiceFailed = true;
        localServiceVerified = false;
        scheduleLocalServiceRestart();
      });
      child.once("exit", () => {
        if (localService !== child) return;
        localService = undefined;
        localServiceVerified = false;
        scheduleLocalServiceRestart();
      });
      try {
        await verifyLocalService(child);
        return;
      } catch (error) {
        lastError = error;
        localServiceVerified = false;
        if (localService === child) localService = undefined;
        if (!child.killed) child.kill();
      }
    }
    localServiceCapability = undefined;
    localServicePort = undefined;
    throw lastError ?? new Error("The managed local service could not start.");
  } finally {
    startingLocalService = false;
  }
}

export function stopLocalService(): void {
  stoppingLocalService = true;
  if (localServiceRestartTimer) {
    clearTimeout(localServiceRestartTimer);
    localServiceRestartTimer = undefined;
  }
  if (localService && !localService.killed) {
    localService.kill();
  }
  localService = undefined;
  localServiceVerified = false;
  localServiceCapability = undefined;
  localServicePort = undefined;
}

async function requestLocalService(request: LocalServiceRequest): Promise<LocalServiceResponse> {
  if (!localServiceVerified || !localServiceCapability) {
    throw new Error("The managed local service has not completed verification.");
  }
  let path: string;
  let init: RequestInit;
  switch (request.operation) {
    case "health":
      path = "/health";
      init = { method: "GET" };
      break;
    case "restoreSession":
      path = "/auth/session";
      init = { method: "GET" };
      break;
    case "logout":
      path = "/auth/logout";
      init = { method: "POST" };
      break;
    case "login":
      path = "/auth/login";
      init = { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request.request) };
      break;
    default:
      throw new Error("The local service request is not allowed.");
  }
  const response = await getManagedServiceSession().fetch(`${managedServiceUrl()}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      Accept: "application/json",
      Origin: rendererOrigin(),
      "X-Workbench-Capability": localServiceCapability,
      ...init.headers,
    },
  });
  return { status: response.status, body: await response.text() };
}

function getDesktopStatus(): DesktopStatus {
  const managed = managesLocalService();
  const baseStatus = {
    serviceMode: managed ? ("managed" as const) : ("attached" as const),
    serviceRunning: managed ? Boolean(localService) && !localServiceFailed : ("unknown" as const),
    apiBaseUrl: localServiceVerified ? managedServiceUrl() : "http://127.0.0.1:0",
  };

  return useDevelopmentAuthBypass
    ? { ...baseStatus, authMode: "developmentBypass", examplesEnabled: true }
    : { ...baseStatus, authMode: "backend", examplesEnabled: false };
}

function isTrustedIpcSender(event: IpcMainInvokeEvent): boolean {
  return (
    event.sender === mainWindow?.webContents &&
    event.senderFrame === event.sender.mainFrame &&
    isAllowedRendererUrl(event.senderFrame.url)
  );
}

async function createMainWindow(): Promise<BrowserWindow> {
  const nativeTitleBarOptions =
    process.platform === "darwin"
      ? {
          titleBarStyle: "hiddenInset" as const,
          trafficLightPosition: { x: 16, y: 13 },
        }
      : {
          titleBarStyle: "hidden" as const,
          titleBarOverlay: {
            color: "#fafafa",
            symbolColor: "#171717",
            height: 40,
          },
        };
  const window = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1024,
    minHeight: 720,
    backgroundColor: "#fafafa",
    ...nativeTitleBarOptions,
    webPreferences: {
      preload: join(mainDirectory, "../preload/index.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });

  mainWindow = window;
  window.once("closed", () => {
    if (mainWindow === window) {
      mainWindow = undefined;
    }
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event, url) => {
    if (!isAllowedRendererUrl(url)) {
      event.preventDefault();
    }
  });
  window.webContents.on("will-redirect", (event, url) => {
    if (!isAllowedRendererUrl(url)) {
      event.preventDefault();
    }
  });

  if (useDevelopmentRenderer) {
    await window.loadURL(developmentRendererUrl);
  } else {
    await window.loadURL(builtRendererOrigin);
  }
  return window;
}

async function startApplication(): Promise<void> {
  session.defaultSession.webRequest.onBeforeRequest({ urls: ["*://*/*"] }, (details, callback) => {
    callback({ cancel: !isAllowedRequestUrl(details.url) });
  });
  getManagedServiceSession().webRequest.onBeforeRequest({ urls: ["*://*/*"] }, (details, callback) => {
    callback({ cancel: !isAllowedManagedServiceRequest(details.url) });
  });

  await startBuiltRendererServer();
  const signingSecret = await provisionSigningSecret();
  registerDesktopIpc({ getDesktopStatus, isTrustedSender: isTrustedIpcSender, requestLocalService });
  await startLocalService(signingSecret);
  await createMainWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      void createMainWindow().catch(handleStartupFailure);
    }
  });
}

function handleStartupFailure(error: unknown): void {
  console.error("WorkBench failed to start.", error);
  stopLocalService();
  stopBuiltRendererServer();
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.destroy();
  }
  dialog.showErrorBox(
    "WorkBench could not start",
    "WorkBench could not complete desktop startup. Check the local setup instructions and try again.",
  );
  app.quit();
}

void app.whenReady().then(startApplication).catch(handleStartupFailure);

app.on("before-quit", () => {
  stopLocalService();
  stopBuiltRendererServer();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
