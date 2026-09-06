import { spawn, spawnSync } from "node:child_process";
import process from "node:process";
import { homedir, platform } from "node:os";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

const repoRoot = fileURLToPath(new URL("../", import.meta.url));
const aiRoot = join(repoRoot, "apps", "ai");
const desktopRoot = join(repoRoot, "apps", "desktop");
const pythonRunner = join(aiRoot, "scripts", "python.mjs");

// Electron dev user-data for the app named "@workbench/desktop".
function defaultDatabasePath() {
  const home = homedir();
  if (platform() === "win32") {
    const appData = process.env.APPDATA || join(home, "AppData", "Roaming");
    return join(appData, "@workbench", "desktop", "workbench.db");
  }
  if (platform() === "darwin") {
    return join(home, "Library", "Application Support", "@workbench", "desktop", "workbench.db");
  }
  const configHome = process.env.XDG_CONFIG_HOME || join(home, ".config");
  return join(configHome, "@workbench", "desktop", "workbench.db");
}

function databasePath() {
  const configured = process.env.WORKBENCH_DB?.trim();
  return configured || defaultDatabasePath();
}

function runForeground(command, args, options = {}) {
  const child = spawn(command, args, { stdio: "inherit", shell: false, ...options });
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.on(signal, () => { child.kill(signal); });
  }
  child.once("error", (error) => {
    process.stderr.write(`Could not start ${command}: ${error.message}\n`);
    process.exitCode = 1;
  });
  child.once("exit", (code) => { process.exitCode = code ?? 1; });
}

// Delegate interpreter selection to the shared runner: WORKBENCH_PYTHON,
// then apps/ai/.venv, then an active virtualenv, then Python on PATH.
function runPython(arguments_) {
  runForeground(process.execPath, [pythonRunner, ...arguments_]);
}

function synchronousSleep(milliseconds) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, milliseconds);
}

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

function stopWindows() {
  // EncodedCommand avoids shell-quoting pitfalls across PowerShell versions.
  const script = [
    "$ErrorActionPreference = 'Stop'",
    "$devMatch = { $_.ProcessId -ne $PID -and $_.CommandLine -and ((",
    "  $_.CommandLine -imatch 'electron' -and",
    "  $_.CommandLine -imatch ([Regex]::Escape($env:WORKBENCH_DEV_REPO_ROOT) + '[\\\\/]apps[\\\\/]desktop')",
    ") -or $_.CommandLine -imatch 'scripts[\\\\/]dev\\.mjs') }",
    "$targets = @(Get-CimInstance Win32_Process | Where-Object $devMatch)",
    "$killFailed = $false",
    "foreach ($target in $targets) {",
    "  taskkill /F /T /PID $target.ProcessId 2>$null",
    "  if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 128) { $killFailed = $true }",
    "}",
    "$remaining = @()",
    "for ($attempt = 0; $attempt -lt 10; $attempt++) {",
    "  Start-Sleep -Milliseconds 200",
    "  $remaining = @(Get-CimInstance Win32_Process | Where-Object $devMatch)",
    "  if ($remaining.Count -eq 0) { break }",
    "}",
    "if ($killFailed) {",
    "  [Console]::Error.WriteLine('app:stop could not terminate one or more development processes.')",
    "  exit 1",
    "}",
    "if ($remaining.Count -gt 0) {",
    "  [Console]::Error.WriteLine(('app:stop timed out; development processes are still running: ' + (($remaining | ForEach-Object { $_.ProcessId }) -join ', ')))",
    "  exit 2",
    "}",
    "if ($targets.Count -gt 0) { Write-Output ('Stopped ' + $targets.Count + ' development process tree(s).') }",
    "exit 0",
  ].join("\n");
  const result = spawnSync(
    "powershell.exe",
    ["-NoProfile", "-NonInteractive", "-EncodedCommand", Buffer.from(script, "utf16le").toString("base64")],
    { cwd: repoRoot, shell: false, stdio: "inherit" },
  );
  if (result.error) {
    fail(`app:stop could not start PowerShell: ${result.error.message}`);
  }
  if (result.status !== 0) {
    fail(`app:stop failed on Windows (PowerShell exited with status ${result.status ?? "unknown"}).`);
  }
}

function stopPosix() {
  const escapedRoot = repoRoot.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const patterns = [
    `[Ee]lectron .*${escapedRoot}/apps/desktop`,
    "scripts/dev\\.mjs",
  ];
  for (const pattern of patterns) {
    const result = spawnSync("pkill", ["-f", pattern], { cwd: repoRoot, shell: false, stdio: "ignore" });
    if (result.error) {
      fail(`app:stop could not run pkill: ${result.error.message}`);
    }
    // Exit 1 means no process matched; anything else is a pkill failure.
    if (result.status !== 0 && result.status !== 1) {
      fail(`app:stop failed: pkill exited with status ${result.status ?? "unknown"}.`);
    }
  }
  const survivors = () => patterns.filter(
    (pattern) => spawnSync("pgrep", ["-f", pattern], { cwd: repoRoot, shell: false, stdio: "ignore" }).status === 0,
  );
  const waitForExit = (timeoutMilliseconds) => {
    const deadline = Date.now() + timeoutMilliseconds;
    while (Date.now() < deadline) {
      if (survivors().length === 0) return true;
      synchronousSleep(100);
    }
    return survivors().length === 0;
  };
  if (waitForExit(5_000)) return;
  for (const pattern of survivors()) {
    spawnSync("pkill", ["-9", "-f", pattern], { cwd: repoRoot, shell: false, stdio: "ignore" });
  }
  if (waitForExit(2_000)) return;
  const listing = patterns
    .map((pattern) => spawnSync("pgrep", ["-fl", pattern], { cwd: repoRoot, shell: false, encoding: "utf8" }).stdout ?? "")
    .join("")
    .trim();
  fail(
    "app:stop timed out; development processes are still running:" +
    (listing ? `\n${listing}` : " (pgrep could not list them)"),
  );
}

function stopProcesses() {
  if (platform() === "win32") {
    process.env.WORKBENCH_DEV_REPO_ROOT = repoRoot;
    stopWindows();
    return;
  }
  stopPosix();
}

const commands = {
  "dev-login": () => {
    runForeground(process.execPath, [join(desktopRoot, "scripts", "dev.mjs")], {
      cwd: desktopRoot,
      env: { ...process.env, WORKBENCH_SKIP_AUTH: "1" },
    });
  },
  stop: stopProcesses,
  "account:list": () => {
    runPython(["-m", "app.provision_account", "--list", "--database-path", databasePath()]);
  },
  "account:secrets": () => {
    runPython([
      "-m", "app.provision_account", "--list", "--show-secrets",
      "--database-path", databasePath(),
    ]);
  },
  "account:provision": () => {
    runPython(["-m", "app.provision_account", "--database-path", databasePath()]);
  },
  "db:reset": () => {
    runPython([join(repoRoot, "scripts", "db_reset.py"), databasePath()]);
  },
};

const command = process.argv[2];
const handler = commands[command ?? ""];
if (!handler) {
  process.stderr.write(
    `Unknown command: ${command ?? "(none)"}\n` +
    `Usage: node scripts/dev_workflow.mjs <${Object.keys(commands).join("|")}>\n`,
  );
  process.exit(1);
}
handler();
