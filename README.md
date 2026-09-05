# WorkBench

A local-first AI workbench for confidential industrial documents. The Windows employee client uses Electron, React, and TypeScript; Python services use FastAPI and Ollama. [PLAN.md](PLAN.md) defines the MVP architecture and acceptance path.

## Current implementation

- `apps/desktop`: employee UI, memory-only drafts, native file selection, and pending local API integration. [Desktop setup](apps/desktop/README.md).
- `apps/ai`: Ollama adapter, typed service contracts, workflow transitions, and approval policy. The FastAPI entry point and complete inspection workflow are not implemented on this branch.
- `apps/web`: static product-site placeholder.
- `apps/api`: reserved and unused; it has no validation tasks.
- `packages`: reserved for framework-neutral code with multiple consumers. Applications must not import one another.

## Developer setup

Use Node 22.18+ and the pnpm version in `package.json`. Python 3.11 is the tested service baseline. Install development dependencies before going offline; application execution must not download models or contact cloud services.

```sh
pnpm install --frozen-lockfile
python3.11 -m venv apps/ai/.venv
apps/ai/.venv/bin/python -m pip install -r apps/ai/requirements.txt
```

On Windows, create the environment with `py -3.11 -m venv apps/ai/.venv`, then install with `apps/ai/.venv/Scripts/python.exe -m pip install -r apps/ai/requirements.txt`.

Python workspace commands select `WORKBENCH_PYTHON` first, then `apps/ai/.venv`, then an activated virtualenv/Conda environment, then Python on PATH. An explicit invalid interpreter fails instead of silently switching environments. Commands run from `apps/ai`; never patch `sys.path`. No runner command installs dependencies automatically.

## Verification loop

```sh
pnpm check                                      # all lint and typechecks; report independent failures together
pnpm test                                       # Python unit tests and desktop state tests; no live models
pnpm build                                      # desktop and web production assets
pnpm verify                                     # all three, in that order
pnpm --filter @workbench/ai test tests/tools      # focused Python tests
pnpm --filter @workbench/desktop test             # fast desktop state checks without Electron
```

Validation tasks always execute. Production builds cache and restore `dist/**`. CI runs `pnpm verify` on Linux and Windows; a bot-review request is not a substitute for these checks. Python requirements currently use version ranges, so environments are not fully pinned.

Live model verification is separate and requires Ollama with approved models already loaded onto the workstation:

```sh
pnpm --filter @workbench/ai test:live
```

That command enables the opt-in model tests; it does not download a model or substitute fixtures. Unit tests, a successful build, and the live adapter test do not establish the full inspection demo. Follow the acceptance checklist in `PLAN.md` on the target Windows machine and report any untested steps.

For UI work, run `pnpm --filter @workbench/desktop dev`. The development-only bypass is documented in the desktop README. Source changes to Electron main/preload require restarting the development command; renderer changes use Vite hot reload.
