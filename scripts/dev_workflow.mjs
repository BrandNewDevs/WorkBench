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

function stopProcesses() {
  const escapedRoot = repoRoot.replaceAll("\\", "\\\\").replace(/[.*+?^${}()|[\]]/g, "\\$&");
  if (platform() === "win32") {
    process.env.WORKBENCH_DEV_REPO_ROOT = repoRoot;
    const script = [
      "$root = [Regex]::Escape($env:WORKBENCH_DEV_REPO_ROOT)",
      "$targets = Get-CimInstance Win32_Process | Where-Object {",
      "  $_.ProcessId -ne $PID -and $_.CommandLine -and ((",
      "    $_.CommandLine -imatch 'electron' -and",
      "    $_.CommandLine -imatch ($root + '[\\\\/]apps[\\\\/]desktop')",
      "  ) -or $_.CommandLine -imatch 'scripts[\\\\/]dev\\.mjs')",
      "}",
      "foreach ($target in $targets) { taskkill /F /T /PID $target.ProcessId 2>$null }",
    ].join("\n");
    spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], {
      cwd: repoRoot,
      shell: false,
      stdio: "inherit",
    });
    return;
  }
  const electronPattern = `[Ee]lectron .*${escapedRoot}/apps/desktop`;
  const runnerPattern = "scripts/dev\\.mjs";
  spawnSync("pkill", ["-f", electronPattern], { cwd: repoRoot, shell: false, stdio: "ignore" });
  spawnSync("pkill", ["-f", runnerPattern], { cwd: repoRoot, shell: false, stdio: "ignore" });
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
