import { spawn, type ChildProcess } from "node:child_process";
import { app, BrowserWindow, session, type IpcMainInvokeEvent } from "electron";
import { dirname, join, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { registerDesktopIpc } from "./ipc";
import { LOCAL_API_ORIGIN } from "../shared/contracts";
import type { DesktopStatus } from "../shared/contracts";

const mainDirectory = dirname(fileURLToPath(import.meta.url));
const rendererEntryPath = join(mainDirectory, "../../renderer/index.html");
const builtRendererUrl = pathToFileURL(rendererEntryPath).href;
const localApiUrl = new URL(LOCAL_API_ORIGIN);
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

function isDevelopmentRendererUrl(value: string): boolean {
  try {
    return new URL(value).origin === new URL(developmentRendererUrl).origin;
  } catch {
    return false;
  }
}

function isAllowedRendererUrl(value: string): boolean {
  if (useDevelopmentRenderer) {
    return isDevelopmentRendererUrl(value);
  }
  return value === builtRendererUrl;
}

function isRendererResourceUrl(value: string): boolean {
  try {
    const url = new URL(value);
    if (url.protocol !== "file:") {
      return false;
    }
    const rendererDirectory = resolve(join(mainDirectory, "../../renderer"));
    const resourcePath = resolve(fileURLToPath(url));
    return resourcePath === rendererDirectory || resourcePath.startsWith(`${rendererDirectory}${sep}`);
  } catch {
    return false;
  }
}

function isAllowedRequestUrl(value: string): boolean {
  if (isRendererResourceUrl(value)) {
    return true;
  }

  try {
    const origin = new URL(value).origin;
    if (origin === LOCAL_API_ORIGIN) {
      return true;
    }
    return useDevelopmentRenderer && origin === new URL(developmentRendererUrl).origin;
  } catch {
    return false;
  }
}

function scheduleLocalServiceRestart(): void {
  if (
    process.env.WORKBENCH_START_LOCAL_SERVICE !== "1" ||
    stoppingLocalService ||
    localService ||
    localServiceRestartTimer
  ) {
    return;
  }

  localServiceRestartTimer = setTimeout(() => {
    localServiceRestartTimer = undefined;
    startLocalService();
  }, localServiceRestartDelayMs);
}

export function startLocalService(): void {
  if (process.env.WORKBENCH_START_LOCAL_SERVICE !== "1" || localService || localServiceRestartTimer) {
    return;
  }

  stoppingLocalService = false;
  localServiceFailed = false;
  const aiDirectory = resolve(app.getAppPath(), "../ai");
  const executable = process.platform === "win32" ? "python.exe" : "python3";
  const child = spawn(
    executable,
    ["-m", "uvicorn", "app.main:app", "--host", localApiUrl.hostname, "--port", localApiUrl.port],
    {
      cwd: aiDirectory,
      shell: false,
      windowsHide: true,
      stdio: "ignore",
      env: { ...process.env, WORKBENCH_LOCAL_ONLY: "1" },
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
  const managed = process.env.WORKBENCH_START_LOCAL_SERVICE === "1";
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
    await window.loadFile(rendererEntryPath);
  }
  return window;
}

app.whenReady().then(async () => {
  session.defaultSession.webRequest.onBeforeRequest({ urls: ["*://*/*"] }, (details, callback) => {
    callback({ cancel: !isAllowedRequestUrl(details.url) });
  });

  registerDesktopIpc({ getDesktopStatus, isTrustedSender: isTrustedIpcSender });
  startLocalService();
  await createMainWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      void createMainWindow();
    }
  });
});

app.on("before-quit", () => {
  stopLocalService();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
