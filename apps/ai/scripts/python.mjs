import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const serviceRoot = fileURLToPath(new URL("../", import.meta.url));
const environmentPython = (root) => join(root, process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const configuredPython = process.env.WORKBENCH_PYTHON;
const localPython = environmentPython(join(serviceRoot, ".venv"));
const activePython = process.env.VIRTUAL_ENV
  ? environmentPython(process.env.VIRTUAL_ENV)
  : process.env.CONDA_PREFIX
    ? join(process.env.CONDA_PREFIX, process.platform === "win32" ? "python.exe" : "bin/python")
    : undefined;
const candidates = configuredPython
  ? [configuredPython]
  : existsSync(localPython)
    ? [localPython]
    : activePython
      ? [activePython]
      : process.platform === "win32" ? ["python.exe"] : ["python3.11", "python3"];

const python = candidates.find((candidate) => {
  const probe = spawnSync(candidate, ["-c", "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"], {
    cwd: serviceRoot,
    shell: false,
    timeout: 5_000,
    stdio: "ignore",
  });
  return probe.status === 0;
});

if (!python) {
  process.stderr.write(
    "Python 3.11+ is required. Create apps/ai/.venv and install apps/ai/requirements.txt, " +
    "activate a Python environment, or set WORKBENCH_PYTHON to its executable. " +
    `Tried: ${candidates.join(", ")}\n`,
  );
  process.exit(1);
}

const args = process.argv.slice(2);
const live = args[0] === "--live";
if (live) args.shift();
const child = spawn(python, args, {
  cwd: serviceRoot,
  shell: false,
  stdio: "inherit",
  env: live ? { ...process.env, WORKBENCH_RUN_LIVE_OLLAMA: "1" } : process.env,
});
child.once("error", (error) => {
  process.stderr.write(`Could not start Python: ${error.message}\n`);
  process.exitCode = 1;
});
child.once("exit", (code) => { process.exitCode = code ?? 1; });
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => { child.kill(signal); });
}
