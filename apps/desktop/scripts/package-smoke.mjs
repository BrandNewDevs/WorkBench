/* global Buffer, URL, fetch, process, setTimeout */

import { createHmac, randomBytes, randomInt, timingSafeEqual } from "node:crypto";
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

const port = randomInt(49_152, 65_536);
const capability = randomBytes(32).toString("base64url");
const signingSecret = randomBytes(48).toString("hex");
const child = spawn(service, [], {
  windowsHide: true,
  env: {
    ...process.env,
    WORKBENCH_APP_HOST: "127.0.0.1",
    WORKBENCH_APP_PORT: String(port),
    WORKBENCH_APP_LOCAL_SERVICE_CAPABILITY: capability,
    WORKBENCH_APP_AUTH_SIGNING_SECRET: signingSecret,
    WORKBENCH_APP_DATABASE_PATH: join(process.env.TEMP ?? serviceRoot, `workbench-smoke-${process.pid}.db`),
    WORKBENCH_APP_CORS_ALLOWED_ORIGINS: '["http://127.0.0.1:5173"]',
  },
  stdio: "ignore",
});
try {
  const nonce = randomBytes(32).toString("hex");
  const expected = createHmac("sha256", capability).update(nonce).digest("hex");
  let verified = false;
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/internal/ready`, {
        headers: { "X-Workbench-Readiness-Nonce": nonce },
      });
      const body = await response.json();
      if (typeof body.proof === "string" && body.proof.length === expected.length && timingSafeEqual(Buffer.from(body.proof), Buffer.from(expected))) {
        verified = true;
        break;
      }
    } catch { /* The frozen service is still starting. */ }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (!verified) throw new Error("Packaged service did not complete the capability handshake.");
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
if (help.code !== 0 || !help.output.includes("usage:")) {
  throw new Error(`Provisioning help exited with ${help.code}.`);
}
const nonInteractive = await runProvision([], "ignore");
if (nonInteractive.code !== 2) {
  throw new Error(`Provisioning tool must reject non-interactive input, got ${nonInteractive.code}.`);
}
