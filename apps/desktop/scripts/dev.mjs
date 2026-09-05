import { spawn } from "node:child_process";
import { get as httpGet } from "node:http";
import { createRequire } from "node:module";
import process from "node:process";

const require = createRequire(import.meta.url);
const electronBinary = require("electron");
const packageManager = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
const viteServerUrl = "http://127.0.0.1:5173";
const children = [];

function run(command, args, extraEnv = {}) {
  const child = spawn(command, args, {
    stdio: "inherit",
    env: { ...process.env, ...extraEnv },
    shell: false,
  });
  children.push(child);
  return child;
}

function stopChildren() {
  for (const child of children) {
    if (!child.killed) {
      child.kill();
    }
  }
}

function waitForVite(url, viteProcess) {
  return new Promise((resolve, reject) => {
    let retryTimer;
    let timeoutTimer;
    let settled = false;

    const cleanup = () => {
      if (retryTimer) {
        globalThis.clearTimeout(retryTimer);
      }
      if (timeoutTimer) {
        globalThis.clearTimeout(timeoutTimer);
      }
      viteProcess.off("error", onError);
      viteProcess.off("exit", onExit);
    };
    const succeed = () => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      resolve();
    };
    const fail = (error) => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      reject(error);
    };
    const retry = () => {
      if (!settled && !retryTimer) {
        retryTimer = globalThis.setTimeout(() => {
          retryTimer = undefined;
          check();
        }, 100);
      }
    };
    const check = () => {
      if (settled) {
        return;
      }
      const request = httpGet(url, (response) => {
        response.resume();
        if (response.statusCode && response.statusCode >= 200 && response.statusCode < 300) {
          succeed();
        } else {
          retry();
        }
      });
      request.on("error", retry);
    };
    const onError = (error) => fail(error);
    const onExit = (code) => {
      fail(new Error(`Vite exited before becoming ready${code === null ? "" : ` (code ${code})`}`));
    };

    viteProcess.once("error", onError);
    viteProcess.once("exit", onExit);
    timeoutTimer = globalThis.setTimeout(() => {
      fail(new Error("Timed out waiting for Vite at http://127.0.0.1:5173"));
    }, 30_000);
    check();
  });
}

process.on("SIGINT", () => {
  stopChildren();
  process.exit(0);
});
process.on("SIGTERM", () => {
  stopChildren();
  process.exit(0);
});

const build = spawn(packageManager, ["run", "build:electron"], { stdio: "inherit", shell: false });
build.on("exit", async (code) => {
  if (code !== 0) {
    process.exitCode = code ?? 1;
    return;
  }

  const vite = run(packageManager, ["exec", "vite", "--host", "127.0.0.1"], {
    WORKBENCH_DEV_RENDERER: "1",
    VITE_DEV_SERVER_URL: viteServerUrl,
  });
  try {
    await waitForVite(viteServerUrl, vite);
  } catch (error) {
    globalThis.console.error(error instanceof Error ? error.message : error);
    stopChildren();
    process.exitCode = 1;
    return;
  }

  const electron = run(electronBinary, ["."], {
    WORKBENCH_DEV_RENDERER: "1",
    VITE_DEV_SERVER_URL: viteServerUrl,
  });
  electron.on("exit", (electronCode) => {
    stopChildren();
    process.exit(electronCode ?? 0);
  });
});
