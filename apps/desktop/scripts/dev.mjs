import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import process from "node:process";
import { fileURLToPath, URL } from "node:url";
import { build, createServer } from "vite";

const require = createRequire(import.meta.url);
const projectRoot = fileURLToPath(new URL("../", import.meta.url));
const development = !process.argv.includes("--built");
let server;
let electron;
let stopping = false;

async function stop(code) {
  if (stopping) return;
  stopping = true;
  electron?.kill();
  await server?.close();
  process.exitCode = code;
}

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => { void stop(0); });
}

async function start() {
  if (development) {
    // Separate output directories let both Electron builds run together.
    await Promise.all([
      build({ root: projectRoot, configFile: `${projectRoot}/vite.main.config.ts` }),
      build({ root: projectRoot, configFile: `${projectRoot}/vite.preload.config.ts` }),
    ]);
    if (stopping) return;
    server = await createServer({ configFile: `${projectRoot}/vite.config.ts` });
    if (!stopping) await server.listen();
    if (stopping) {
      await server.close();
      return;
    }
  }

  const env = {
    ...process.env,
    WORKBENCH_DEV_RENDERER: development ? "1" : "0",
    VITE_DEV_SERVER_URL: "http://127.0.0.1:5173",
  };
  // Electron-based editors may pass this flag to their terminal children.
  delete env.ELECTRON_RUN_AS_NODE;
  electron = spawn(require("electron"), [projectRoot], {
    cwd: projectRoot,
    shell: false,
    stdio: "inherit",
    env,
  });
  electron.once("error", (error) => {
    globalThis.console.error(error.message);
    void stop(1);
  });
  electron.once("exit", (code) => { void stop(code ?? 1); });
}

try {
  await start();
} catch (error) {
  globalThis.console.error(error instanceof Error ? error.message : error);
  await stop(1);
}
