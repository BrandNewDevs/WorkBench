import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createServer, type Server } from "node:http";
import { randomBytes } from "node:crypto";
import { app, BrowserWindow, dialog, session, type IpcMainInvokeEvent } from "electron";
import { dirname, extname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { registerDesktopIpc } from "./ipc";
import { LOCAL_API_ORIGIN } from "../shared/contracts";
import type { DesktopStatus } from "../shared/contracts";

const mainDirectory = dirname(fileURLToPath(import.meta.url));
const rendererDirectory = resolve(join(mainDirectory, "../../renderer"));
const rendererEntryPath = join(rendererDirectory, "index.html");
const localApiUrl = new URL(LOCAL_API_ORIGIN);
const builtRendererOrigin = "http://127.0.0.1:5173";
const useDevelopmentRenderer = process.env.WORKBENCH_DEV_RENDERER === "1";
// Keep this decision in Electron main. The renderer can only receive the resulting, typed mode over trusted IPC.
const useDevelopmentAuthBypass =
  !app.isPackaged && useDevelopmentRenderer && process.env.WORKBENCH_SKIP_AUTH === "1";
const configuredRendererUrl = process.env.VITE_DEV_SERVER_URL ?? "http://127.0.0.1:5173";
const localServiceRestartDelayMs = 1_000;
let mainWindow: BrowserWindow | undefined;
let localService: ChildProcess | undefined;
let localServiceRestartTimer: ReturnType<typeof setTimeout> | undefined;
let localServiceFailed = false;
let stoppingLocalService = false;
let builtRendererServer: Server | undefined;
let localSigningSecret: string | undefined;

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
    const origin = new URL(value).origin;
    return origin === LOCAL_API_ORIGIN || origin === rendererOrigin();
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
  return app.isPackaged || process.env.WORKBENCH_START_LOCAL_SERVICE === "1";
}

function scheduleLocalServiceRestart(): void {
  if (!managesLocalService() || stoppingLocalService || localService || localServiceRestartTimer) {
    return;
  }

  localServiceRestartTimer = setTimeout(() => {
    localServiceRestartTimer = undefined;
    startLocalService();
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
    const serviceDirectory = join(process.resourcesPath, "service");
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
    arguments: ["-m", "uvicorn", "app.main:app", "--host", localApiUrl.hostname, "--port", localApiUrl.port],
    workingDirectory: aiDirectory,
  };
}

export function startLocalService(signingSecret?: string): void {
  localSigningSecret = signingSecret ?? localSigningSecret;
  if (!managesLocalService() || localService || localServiceRestartTimer) {
    return;
  }

  const launch = localServiceLaunch();
  stoppingLocalService = false;
  localServiceFailed = false;
  const child = spawn(
    launch.executable,
    launch.arguments,
    {
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
        WORKBENCH_APP_DATABASE_PATH: join(app.getPath("userData"), "workbench.db"),
      },
    },
  );
  localService = child;
  child.once("error", () => {
    if (localService === child) {
      localService = undefined;
      localServiceFailed = true;
    }
    scheduleLocalServiceRestart();
  });
  child.once("exit", () => {
    if (localService === child) {
      localService = undefined;
    }
    scheduleLocalServiceRestart();
  });
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
}

function getDesktopStatus(): DesktopStatus {
  const managed = managesLocalService();
  const baseStatus = {
    serviceMode: managed ? ("managed" as const) : ("attached" as const),
    serviceRunning: managed ? Boolean(localService) && !localServiceFailed : ("unknown" as const),
    apiBaseUrl: LOCAL_API_ORIGIN,
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

  await startBuiltRendererServer();
  const signingSecret = await provisionSigningSecret();
  registerDesktopIpc({ getDesktopStatus, isTrustedSender: isTrustedIpcSender });
  startLocalService(signingSecret);
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
