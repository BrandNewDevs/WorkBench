/* global Buffer, URL, process, setTimeout, clearTimeout */

import { createHmac, randomBytes, randomUUID, timingSafeEqual } from "node:crypto";
import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

if (process.platform !== "win32") {
  throw new Error("The packaged-service smoke test runs only on Windows.");
}

const desktopRoot = fileURLToPath(new URL("../", import.meta.url));
const serviceRoot = join(desktopRoot, "dist", "win-unpacked", "resources", "service");
const service = join(serviceRoot, "workbench-service", "workbench-service.exe");
const provision = join(serviceRoot, "workbench-provision-account", "workbench-provision-account.exe");
for (const executable of [service, provision]) {
  if (!existsSync(executable)) throw new Error(`Packaged executable is missing: ${executable}`);
}

const capability = randomBytes(32).toString("base64url");
const signingSecret = randomBytes(48).toString("hex");
const child = spawn(service, [], {
  windowsHide: true,
  env: {
    ...process.env,
    WORKBENCH_APP_LOCAL_SERVICE_CAPABILITY: capability,
    WORKBENCH_APP_AUTH_SIGNING_SECRET: signingSecret,
    WORKBENCH_APP_DATABASE_PATH: join(process.env.TEMP ?? serviceRoot, `workbench-smoke-${process.pid}.db`),
    WORKBENCH_APP_CORS_ALLOWED_ORIGINS: '["http://127.0.0.1:5173"]',
  },
  stdio: ["pipe", "pipe", "pipe"],
});

function request(path, headers = {}) {
  const id = randomUUID();
  const frame = JSON.stringify({ id, method: "GET", path, headers, body: "" });
  return new Promise((resolve, reject) => {
    let output = "";
    const timeout = setTimeout(() => reject(new Error("Packaged IPC service did not respond.")), 5_000);
    const onData = (chunk) => {
      output += chunk;
      const end = output.indexOf("\n");
      if (end < 0) return;
      child.stdout.off("data", onData);
      clearTimeout(timeout);
      const response = JSON.parse(output.slice(0, end));
      if (response.id !== id) reject(new Error("Packaged IPC service returned the wrong response."));
      else resolve(response);
    };
    child.stdout.on("data", onData);
    child.stdin.write(`${frame}\n`, (error) => error && reject(error));
  });
}

try {
  const nonce = randomBytes(32).toString("hex");
  const expected = createHmac("sha256", capability).update(nonce).digest("hex");
  const response = await request("/internal/ready", { "X-Workbench-Readiness-Nonce": nonce });
  const body = JSON.parse(Buffer.from(response.body, "base64").toString("utf8"));
  if (typeof body.proof !== "string" || body.proof.length !== expected.length || !timingSafeEqual(Buffer.from(body.proof), Buffer.from(expected))) {
    throw new Error("Packaged service did not complete the capability handshake over its inherited pipe.");
  }
} finally {
  child.kill();
}

function runProvision(commandArguments, stdio) {
  return new Promise((resolve, reject) => {
    const provisionChild = spawn(provision, commandArguments, { windowsHide: true, stdio });
    let output = "";
    provisionChild.stdout?.on("data", (chunk) => { output += chunk; });
    provisionChild.once("error", reject);
    provisionChild.once("exit", (code) => resolve({ code, output }));
  });
}

const help = await runProvision(["--help"], "pipe");
if (help.code !== 0 || !help.output.includes("usage:")) throw new Error(`Provisioning help exited with ${help.code}.`);
const nonInteractive = await runProvision([], "ignore");
if (nonInteractive.code !== 2) throw new Error(`Provisioning tool must reject non-interactive input, got ${nonInteractive.code}.`);
