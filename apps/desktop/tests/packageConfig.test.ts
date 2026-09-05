import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { test } from "node:test";

interface DesktopPackage {
  scripts: Record<string, string>;
  build: {
    asar: boolean;
    extraResources: Array<{ from: string; to: string; filter: string[] }>;
    win: { target: string[] };
  };
}

const desktopPackage = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8")) as DesktopPackage;

test("Windows distribution includes the frozen local service", () => {
  assert.equal(desktopPackage.scripts["dist:win"], "pnpm build && pnpm build:service && electron-builder --win nsis && node scripts/package-smoke.mjs");
  assert.equal(desktopPackage.build.asar, true);
  assert.deepEqual(desktopPackage.build.extraResources, [
    { from: "dist/service", to: "service", filter: ["**/*"] },
  ]);
  assert.deepEqual(desktopPackage.build.win.target, ["nsis"]);
  const buildService = readFileSync(new URL("../scripts/build-service.mjs", import.meta.url), "utf8");
  assert.match(buildService, /workbench-provision-account/);
  assert.equal(existsSync(new URL("../scripts/package-smoke.mjs", import.meta.url)), true);
});

test("main-process service traffic is isolated from renderer traffic", () => {
  const main = readFileSync(new URL("../src/main/index.ts", import.meta.url), "utf8");

  assert.match(main, /session\.fromPartition\("workbench-managed-service"\)/);
  assert.match(main, /credentials: "omit",\s*headers: \{ "X-Workbench-Readiness-Nonce": nonce \}/);
  assert.doesNotMatch(main, /internal\/ready[\s\S]{0,200}X-Workbench-Capability/);
  assert.match(main, /credentials: "include"/);
  assert.match(main, /localServiceStartAttempts = 3/);
  assert.match(main, /getManagedServiceSession\(\)\.webRequest\.onBeforeRequest/);
});

test("startup storage cleanup is covered by the restart guard reset", () => {
  const main = readFileSync(new URL("../src/main/index.ts", import.meta.url), "utf8");

  assert.match(
    main,
    /startingLocalService = true;[\s\S]*?try \{\s+await getManagedServiceSession\(\)\.clearStorageData\(\{ storages: \["cookies"\] \}\);[\s\S]*?finally \{\s+startingLocalService = false;/,
  );
});

test("local service startup keeps bounded, redacted diagnostics", () => {
  const main = readFileSync(new URL("../src/main/index.ts", import.meta.url), "utf8");

  assert.match(main, /join\(\s*aiDirectory,\s+"\.venv"/);
  assert.match(main, /stdio: \["ignore", "pipe", "pipe"\]/);
  assert.match(main, /localServiceDiagnosticLimitBytes = 32 \* 1024/);
  assert.match(main, /appendFile\(startupLogPath\(\), line, "utf8"\)/);
  assert.match(main, /exit code=\$\{exitCode\}, signal=\$\{signal\}/);
  assert.match(main, /password\|passwd\|secret\|token\|authorization\|cookie/);
});
