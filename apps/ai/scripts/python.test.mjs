import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { test } from "node:test";
import process from "node:process";
import { fileURLToPath, URL } from "node:url";

const runner = fileURLToPath(new URL("./python.mjs", import.meta.url));
const serviceRoot = fileURLToPath(new URL("../", import.meta.url));

test("runner uses the service import root and preserves literal arguments", () => {
  const literal = "spaces ; $(do-not-execute) `not-a-command`";
  const result = spawnSync(process.execPath, [runner, "-c", "import app, sys; print(sys.argv[1])", literal], {
    cwd: fileURLToPath(new URL("../../../", import.meta.url)),
    encoding: "utf8",
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), literal);
});

test("runner preserves Python failure exit codes", () => {
  const result = spawnSync(process.execPath, [runner, "-c", "raise SystemExit(17)"], { encoding: "utf8" });
  assert.equal(result.status, 17, result.stderr);
});

test("an invalid explicit interpreter fails with setup instructions", () => {
  const result = spawnSync(process.execPath, [runner, "--version"], {
    encoding: "utf8",
    env: { ...process.env, WORKBENCH_PYTHON: `${serviceRoot}/missing-python` },
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Python 3\.11\+ is required/);
  assert.match(result.stderr, /requirements\.txt/);
});

test("live verification explicitly enables the otherwise skipped model checks", () => {
  const result = spawnSync(process.execPath, [runner, "--live", "-c", "import os; print(os.environ['WORKBENCH_RUN_LIVE_OLLAMA'])"], {
    encoding: "utf8",
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), "1");
});
