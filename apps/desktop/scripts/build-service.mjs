import { existsSync, rmSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath, URL } from "node:url";
import { join } from "node:path";
import process from "node:process";

const desktopRoot = fileURLToPath(new URL("../", import.meta.url));
const aiRoot = fileURLToPath(new URL("../../ai/", import.meta.url));
const outputRoot = join(desktopRoot, "dist", "service");
const configuredPython = process.env.WORKBENCH_PYTHON?.trim();
const virtualEnvironmentPython = join(
  aiRoot,
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);
const python = configuredPython || (existsSync(virtualEnvironmentPython) ? virtualEnvironmentPython : "python3");

if (process.platform !== "win32") {
  throw new Error("Windows service bundles must be built on Windows with the target Python environment.");
}

rmSync(outputRoot, { recursive: true, force: true });
function freeze(name, entryPoint) {
  const result = spawnSync(
    python,
    [
      "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--name", name,
      "--distpath", outputRoot, "--workpath", join(desktopRoot, ".pyinstaller-work", name),
      "--specpath", join(desktopRoot, ".pyinstaller-spec"), "--collect-all", "pwdlib",
      "--collect-all", "pydantic", "--collect-all", "pydantic_core", entryPoint,
    ],
    { cwd: aiRoot, shell: false, stdio: "inherit" },
  );
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`PyInstaller exited with status ${result.status ?? "unknown"}.`);
}

freeze("workbench-service", "app/packaged_service.py");
freeze("workbench-provision-account", "app/provision_account.py");
for (const executable of ["workbench-service", "workbench-provision-account"]) {
  if (!existsSync(join(outputRoot, executable, `${executable}.exe`))) {
    throw new Error(`PyInstaller did not produce dist/service/${executable}/${executable}.exe.`);
  }
}
