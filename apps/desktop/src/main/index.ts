import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { appendFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { createServer, type Server } from "node:http";
import { createHmac, randomBytes, randomUUID, timingSafeEqual } from "node:crypto";
import { app, BrowserWindow, dialog, session, type IpcMainInvokeEvent, type Session } from "electron";
import { dirname, extname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { registerDesktopIpc } from "./ipc";
import type { DesktopStatus, LocalServiceRequest, LocalServiceResponse } from "../shared/contracts";

const mainDirectory = dirname(fileURLToPath(import.meta.url));
const rendererDirectory = resolve(join(mainDirectory, "../../renderer"));
const rendererEntryPath = join(rendererDirectory, "index.html");
let builtRendererOrigin = "http://127.0.0.1:0";
const useDevelopmentRenderer = process.env.WORKBENCH_DEV_RENDERER === "1";
// Keep this decision in Electron main. The renderer can only receive the resulting, typed mode over trusted IPC.
const useDevelopmentAuthBypass =
  !app.isPackaged && useDevelopmentRenderer && process.env.WORKBENCH_SKIP_AUTH === "1";
const configuredRendererUrl = process.env.VITE_DEV_SERVER_URL ?? "http://127.0.0.1:5173";
const localServiceRestartDelayMs = 1_000;
const localServiceStartAttempts = 3;
const localServiceDiagnosticLimitBytes = 32 * 1024;
const startupLogFileName = "startup.log";
let managedServiceSession: Session | undefined;
let mainWindow: BrowserWindow | undefined;
let localService: ChildProcess | undefined;
let localServiceRestartTimer: ReturnType<typeof setTimeout> | undefined;
let localServiceFailed = false;
let stoppingLocalService = false;
let builtRendererServer: Server | undefined;
let localSigningSecret: string | undefined;
const managedServiceCookieUrl = "http://127.0.0.1/";
const localServiceFrameLimitBytes = 1024 * 1024;
const localServiceRequestTimeoutMs = 5_000;
let localServiceCapability: string | undefined;
let localServiceVerified = false;
let startingLocalService = false;
let localServiceResponseBuffer = "";
interface PendingLocalServiceRequest {
  resolve: (response: LocalServiceResponse & { headers: readonly [string, string][] }) => void;
  reject: (error: Error) => void;
  timeout: ReturnType<typeof setTimeout>;
}
const localServiceRequests = new Map<string, PendingLocalServiceRequest>();
let startupLogQueue = Promise.resolve();

class BoundedDiagnostic {
  private readonly chunks: string[] = [];
  private byteLength = 0;
  private truncated = false;

  append(value: string | Buffer): void {
    if (this.truncated) return;
    const remaining = localServiceDiagnosticLimitBytes - this.byteLength;
    if (remaining <= 0) {
      this.truncated = true;
      return;
    }

    const bytes = Buffer.from(value).subarray(0, remaining);
    this.chunks.push(bytes.toString("utf8"));
    this.byteLength += bytes.byteLength;
    if (Buffer.byteLength(value) > remaining) this.truncated = true;
  }

  toString(): string {
    const output = this.chunks.join("");
    return this.truncated ? `${output}\n[output truncated after ${localServiceDiagnosticLimitBytes} bytes]` : output;
  }
}

interface LocalServiceAttemptDiagnostics {
  stdout: BoundedDiagnostic;
  stderr: BoundedDiagnostic;
  sensitiveValues: string[];
  exitObserved: boolean;
  exitCode: number | null | undefined;
  exitSignal: NodeJS.Signals | null | undefined;
  spawnError: string | undefined;
}

function newLocalServiceAttemptDiagnostics(capability: string | undefined): LocalServiceAttemptDiagnostics {
  return {
    stdout: new BoundedDiagnostic(),
    stderr: new BoundedDiagnostic(),
    sensitiveValues: [localSigningSecret, process.env.WORKBENCH_APP_AUTH_SIGNING_SECRET, capability]
      .filter((value): value is string => Boolean(value)),
    exitObserved: false,
    exitCode: undefined,
    exitSignal: undefined,
    spawnError: undefined,
  };
}

function redactDiagnostic(value: string, sensitiveValues: readonly string[] = []): string {
  let redacted = value;
  const environmentSecrets = Object.entries(process.env)
    .filter(([name]) => /(password|passwd|secret|token|authorization|cookie)/i.test(name))
    .map(([, secret]) => secret);
  const values = new Set([
    ...sensitiveValues,
    ...environmentSecrets,
    process.env.WORKBENCH_APP_AUTH_SIGNING_SECRET,
    process.env.WORKBENCH_APP_LOCAL_SERVICE_CAPABILITY,
  ]);
  for (const sensitiveValue of values) {
    if (sensitiveValue) redacted = redacted.split(sensitiveValue).join("[REDACTED]");
  }
  redacted = redacted.replace(/\b[a-f0-9]{96}\b/gi, "[REDACTED]");
  redacted = redacted.replace(
    /(["']?(?:password|passwd|secret|token|authorization|cookie)[\\w-]*["']?\s*[=:]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)/gi,
    "$1[REDACTED]",
  );
  return redacted;
}

function startupLogPath(): string {
  return join(app.getPath("userData"), startupLogFileName);
}

function writeStartupLog(message: string, sensitiveValues: readonly string[] = []): Promise<void> {
  const line = `${new Date().toISOString()} ${redactDiagnostic(message, sensitiveValues)}\n`;
  startupLogQueue = startupLogQueue.then(async () => {
    try {
      await mkdir(app.getPath("userData"), { recursive: true });
      await appendFile(startupLogPath(), line, "utf8");
    } catch (error) {
      console.error(
        "WorkBench could not write its startup log.",
        error instanceof Error ? error.message : String(error),
      );
    }
  });
  return startupLogQueue;
}

function localServiceProcessState(attempt: LocalServiceAttemptDiagnostics): string {
  const exitCode = attempt.exitCode === undefined ? "unavailable" : attempt.exitCode === null ? "none" : String(attempt.exitCode);
  const signal = attempt.exitSignal ?? "none";
  return `exit code=${exitCode}, signal=${signal}`;
}

function localServiceStartupError(error: unknown, attempt: LocalServiceAttemptDiagnostics): Error {
  const reason = redactDiagnostic(error instanceof Error ? error.message : String(error), attempt.sensitiveValues);
  const stdout = redactDiagnostic(attempt.stdout.toString(), attempt.sensitiveValues) || "(empty)";
  const stderr = redactDiagnostic(attempt.stderr.toString(), attempt.sensitiveValues) || "(empty)";
  const spawnError = attempt.spawnError
    ? `, spawn error=${redactDiagnostic(attempt.spawnError, attempt.sensitiveValues)}`
    : "";
  return new Error(
    `${reason} (${localServiceProcessState(attempt)}${spawnError})\n` +
      `FastAPI stdout:\n${stdout}\nFastAPI stderr:\n${stderr}`,
  );
}

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

function clearManagedLocalService(child?: ChildProcess): void {
  if (child && localService !== child) return;
  for (const request of localServiceRequests.values()) {
    clearTimeout(request.timeout);
    request.reject(new Error("The managed local service pipe closed."));
  }
  localServiceRequests.clear();
  localServiceResponseBuffer = "";
  localService = undefined;
  localServiceVerified = false;
  localServiceCapability = undefined;
}

function managedLocalServiceIsRunning(): boolean {
  return Boolean(localService && localService.exitCode === null && !localService.killed && localService.stdin?.writable);
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
      server.listen(0, origin.hostname, () => {
        server.off("error", rejectServer);
        const address = server.address();
        if (!address || typeof address === "string") {
          rejectServer(new Error("The packaged renderer did not receive a loopback port."));
          return;
        }
        builtRendererOrigin = `http://${origin.hostname}:${address.port}`;
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
    ".venv",
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
    arguments: ["-m", "app.ipc_service"],
    workingDirectory: aiDirectory,
  };
}

interface LocalServiceFrame {
  id: string;
  status: number;
  headers: readonly [string, string][];
  body: string;
}

function receiveLocalServiceFrame(value: string): void {
  let frame: LocalServiceFrame;
  try {
    frame = JSON.parse(value) as LocalServiceFrame;
  } catch {
    return;
  }
  if (!frame || typeof frame.id !== "string" || !Number.isInteger(frame.status) ||
    !Array.isArray(frame.headers) || typeof frame.body !== "string") return;
  const request = localServiceRequests.get(frame.id);
  if (!request) return;
  localServiceRequests.delete(frame.id);
  clearTimeout(request.timeout);
  try {
    request.resolve({ status: frame.status, headers: frame.headers, body: Buffer.from(frame.body, "base64").toString("utf8") });
  } catch {
    request.reject(new Error("The managed local service sent an invalid response."));
  }
}

function timeoutLocalServiceRequest(id: string, child: ChildProcess): void {
  const request = localServiceRequests.get(id);
  if (!request) return;
  localServiceRequests.delete(id);
  clearTimeout(request.timeout);
  request.reject(new Error("The managed local service request timed out."));
  if (localService !== child) return;
  if (!child.killed) child.kill();
  clearManagedLocalService(child);
  localServiceFailed = true;
  scheduleLocalServiceRestart();
}

function handleLocalServiceOutput(chunk: Buffer | string): void {
  localServiceResponseBuffer += Buffer.from(chunk).toString("utf8");
  if (Buffer.byteLength(localServiceResponseBuffer) > localServiceFrameLimitBytes * 2) {
    clearManagedLocalService();
    return;
  }
  let lineEnd = localServiceResponseBuffer.indexOf("\n");
  while (lineEnd >= 0) {
    const line = localServiceResponseBuffer.slice(0, lineEnd);
    localServiceResponseBuffer = localServiceResponseBuffer.slice(lineEnd + 1);
    if (Buffer.byteLength(line) <= localServiceFrameLimitBytes) receiveLocalServiceFrame(line);
    lineEnd = localServiceResponseBuffer.indexOf("\n");
  }
}

async function sendLocalServiceRequest(
  path: string,
  method: "GET" | "POST",
  headers: Record<string, string>,
  body?: string,
): Promise<LocalServiceResponse & { headers: readonly [string, string][] }> {
  const child = localService;
  if (!child || !managedLocalServiceIsRunning()) throw new Error("The managed local service is no longer running.");
  const id = randomUUID();
  const frame = JSON.stringify({ id, path, method, headers, body: Buffer.from(body ?? "").toString("base64") });
  if (Buffer.byteLength(frame) > localServiceFrameLimitBytes) throw new Error("The local service request is too large.");
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => timeoutLocalServiceRequest(id, child), localServiceRequestTimeoutMs);
    localServiceRequests.set(id, { resolve, reject, timeout });
    child.stdin?.write(`${frame}\n`, (error) => {
      if (!error) return;
      const request = localServiceRequests.get(id);
      if (!request) return;
      clearTimeout(request.timeout);
      localServiceRequests.delete(id);
      reject(error);
    });
  });
}

async function verifyLocalService(child: ChildProcess): Promise<void> {
  const capability = localServiceCapability;
  if (!capability) throw new Error("The managed local service has no capability.");
  const nonce = randomBytes(32).toString("hex");
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (localService !== child) throw new Error("The managed local service stopped during startup.");
    try {
      const response = await sendLocalServiceRequest("/internal/ready", "GET", { "X-Workbench-Readiness-Nonce": nonce });
      const body = JSON.parse(response.body) as { proof?: unknown };
      const expected = createHmac("sha256", capability).update(nonce).digest("hex");
      if (typeof body.proof === "string" && body.proof.length === expected.length && timingSafeEqual(Buffer.from(body.proof), Buffer.from(expected))) {
        localServiceVerified = true;
        return;
      }
    } catch { /* The child may still be starting its private pipe loop. */ }
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 100));
  }
  throw new Error("The managed local service did not prove its launch capability.");
}

async function waitForFailedLocalService(child: ChildProcess, attempt: LocalServiceAttemptDiagnostics): Promise<void> {
  if (!child.killed) child.kill();
  if (attempt.exitObserved || attempt.spawnError) return;
  await new Promise<void>((resolveWait) => {
    const timer = setTimeout(resolveWait, 500);
    const finish = () => {
      clearTimeout(timer);
      resolveWait();
    };
    child.once("exit", finish);
    child.once("error", finish);
  });
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
    for (let attemptNumber = 0; attemptNumber < localServiceStartAttempts; attemptNumber += 1) {
      localServiceCapability = randomBytes(32).toString("base64url");
      localServiceVerified = false;
      const attempt = newLocalServiceAttemptDiagnostics(localServiceCapability);
      const launch = localServiceLaunch();
      await writeStartupLog(
        `Starting FastAPI IPC attempt ${attemptNumber + 1}/${localServiceStartAttempts}: executable=${launch.executable}, cwd=${launch.workingDirectory}`,
        attempt.sensitiveValues,
      );
      const child = spawn(launch.executable, launch.arguments, {
        cwd: launch.workingDirectory,
        shell: false,
        windowsHide: true,
        // These inherited anonymous pipes are the only Electron-to-FastAPI transport.
        // A different local process cannot bind or replace either endpoint after child launch.
        stdio: ["pipe", "pipe", "pipe"],
        env: {
          ...process.env,
          WORKBENCH_LOCAL_ONLY: "1",
          WORKBENCH_APP_AUTH_SIGNING_SECRET:
            localSigningSecret ?? process.env.WORKBENCH_APP_AUTH_SIGNING_SECRET,
          WORKBENCH_APP_CORS_ALLOWED_ORIGINS: JSON.stringify([rendererOrigin()]),
          WORKBENCH_APP_LOCAL_SERVICE_CAPABILITY: localServiceCapability,
          WORKBENCH_APP_DATABASE_PATH: join(app.getPath("userData"), "workbench.db"),
        },
      });
      child.stdout?.on("data", handleLocalServiceOutput);
      child.stderr?.on("data", (chunk: Buffer | string) => attempt.stderr.append(chunk));
      localService = child;
      child.once("error", (error) => {
        attempt.spawnError = error.message;
        void writeStartupLog(
          `FastAPI attempt ${attemptNumber + 1} failed to spawn: ${error.message}`,
          attempt.sensitiveValues,
        );
        if (localService !== child) return;
        clearManagedLocalService(child);
        localServiceFailed = true;
        scheduleLocalServiceRestart();
      });
      child.once("exit", (code, signal) => {
        attempt.exitObserved = true;
        attempt.exitCode = code;
        attempt.exitSignal = signal;
        void writeStartupLog(
          `FastAPI attempt ${attemptNumber + 1} exited with ${localServiceProcessState(attempt)}. stderr=${redactDiagnostic(attempt.stderr.toString(), attempt.sensitiveValues) || "(empty)"}`,
          attempt.sensitiveValues,
        );
        if (localService !== child) return;
        clearManagedLocalService(child);
        scheduleLocalServiceRestart();
      });
      try {
        await verifyLocalService(child);
        return;
      } catch (error) {
        await waitForFailedLocalService(child, attempt);
        const detailedError = localServiceStartupError(error, attempt);
        lastError = detailedError;
        await writeStartupLog(
          `FastAPI attempt ${attemptNumber + 1} failed:\n${detailedError.message}`,
          attempt.sensitiveValues,
        );
        localServiceVerified = false;
        clearManagedLocalService(child);
      }
    }
    localServiceCapability = undefined;
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
  clearManagedLocalService();
}

async function requestLocalService(request: LocalServiceRequest): Promise<LocalServiceResponse> {
  if (!localServiceVerified || !localServiceCapability || !managedLocalServiceIsRunning()) {
    clearManagedLocalService();
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
  // The cookie remains in Electron's private partition. It is serialized only into the
  // child-owned pipe after capability verification, so a replacement local listener has no path to it.
  const cookies = await getManagedServiceSession().cookies.get({ url: managedServiceCookieUrl });
  const cookieHeader = cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join("; ");
  const response = await sendLocalServiceRequest(path, init.method as "GET" | "POST", {
    Accept: "application/json",
    Origin: rendererOrigin(),
    "X-Workbench-Capability": localServiceCapability,
    ...(cookieHeader ? { Cookie: cookieHeader } : {}),
    ...(init.headers as Record<string, string> | undefined),
  }, typeof init.body === "string" ? init.body : undefined);
  for (const [name, value] of response.headers) {
    if (name.toLowerCase() !== "set-cookie") continue;
    const [pair, ...attributes] = value.split(";").map((part) => part.trim());
    const separator = pair.indexOf("=");
    if (separator <= 0) continue;
    const cookieName = pair.slice(0, separator);
    const cookieValue = pair.slice(separator + 1);
    const maxAge = attributes.find((attribute) => attribute.toLowerCase().startsWith("max-age="));
    const seconds = maxAge ? Number(maxAge.slice("max-age=".length)) : undefined;
    await getManagedServiceSession().cookies.set({
      url: managedServiceCookieUrl,
      name: cookieName,
      value: cookieValue,
      path: "/",
      httpOnly: attributes.some((attribute) => attribute.toLowerCase() === "httponly"),
      secure: attributes.some((attribute) => attribute.toLowerCase() === "secure"),
      sameSite: attributes.some((attribute) => attribute.toLowerCase() === "samesite=strict") ? "strict" : "unspecified",
      ...(Number.isFinite(seconds) ? { expirationDate: Date.now() / 1000 + (seconds as number) } : {}),
    });
  }
  return { status: response.status, body: response.body };
}

function getDesktopStatus(): DesktopStatus {
  const managed = managesLocalService();
  const baseStatus = {
    serviceMode: managed ? ("managed" as const) : ("attached" as const),
    serviceRunning: managed ? Boolean(localService) && !localServiceFailed : ("unknown" as const),
    apiBaseUrl: "ipc://electron-managed",
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
  await writeStartupLog("WorkBench startup begin");
  session.defaultSession.webRequest.onBeforeRequest({ urls: ["*://*/*"] }, (details, callback) => {
    callback({ cancel: !isAllowedRequestUrl(details.url) });
  });
  await startBuiltRendererServer();
  const signingSecret = await provisionSigningSecret();
  registerDesktopIpc({ getDesktopStatus, isTrustedSender: isTrustedIpcSender, requestLocalService });
  await startLocalService(signingSecret);
  await createMainWindow();
  await writeStartupLog("WorkBench startup complete");

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      void createMainWindow().catch((error) => void handleStartupFailure(error));
    }
  });
}

async function handleStartupFailure(error: unknown): Promise<void> {
  const diagnostic = redactDiagnostic(error instanceof Error ? error.message : String(error));
  console.error("WorkBench failed to start. See the local startup log for details.", diagnostic);
  await writeStartupLog(`WorkBench startup failed:\n${diagnostic}`);
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

void app.whenReady().then(startApplication).catch((error) => void handleStartupFailure(error));

app.on("before-quit", () => {
  stopLocalService();
  stopBuiltRendererServer();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
