import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
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
  assert.equal(desktopPackage.scripts["dist:win"], "pnpm build && pnpm build:service && electron-builder --win nsis");
  assert.equal(desktopPackage.build.asar, true);
  assert.deepEqual(desktopPackage.build.extraResources, [
    { from: "dist/service/workbench-service", to: "service", filter: ["**/*"] },
  ]);
  assert.deepEqual(desktopPackage.build.win.target, ["nsis"]);
});
