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

Python workspace commands select `WORKBENCH_PYTHON` first, then `apps/ai/.venv`, then an activated virtualenv/Conda environment, then Python on PATH. An explicit invalid interpreter fails instead of silently switching environments. Commands run from `apps/ai`; never patch `sys.path`. No runner command installs dependencies automatically. Electron follows the same `WORKBENCH_PYTHON`, `apps/ai/.venv`, then PATH order when it starts FastAPI.

## Developer setup

Use Node 22.18+ and the pnpm version in `package.json`. Python 3.11 is the tested service baseline. Install development dependencies before going offline; application execution must not download models or contact cloud services.

```sh
pnpm install --frozen-lockfile
python3.11 -m venv apps/ai/.venv
apps/ai/.venv/bin/python -m pip install -r apps/ai/requirements.txt
```

On Windows, create the environment with `py -3.11 -m venv apps/ai/.venv`, then install with `apps/ai/.venv/Scripts/python.exe -m pip install -r apps/ai/requirements.txt`.

Python workspace commands select `WORKBENCH_PYTHON` first, then `apps/ai/.venv`, then an activated virtualenv/Conda environment, then Python on PATH. An explicit invalid interpreter fails instead of silently switching environments. Commands run from `apps/ai`; never patch `sys.path`. No runner command installs dependencies automatically. Electron follows the same `WORKBENCH_PYTHON`, `apps/ai/.venv`, then PATH order when it starts FastAPI.

FastAPI stores its default SQLite database in the current user's application-data directory, not in the source tree: `%LOCALAPPDATA%\\WorkBench` on Windows, `~/Library/Application Support/WorkBench` on macOS, and `$XDG_DATA_HOME/workbench` or `~/.local/share/workbench` on Linux. Electron uses that same FastAPI default when it manages the service. For a fresh database, create the first employee from an interactive local terminal before starting the client:

```sh
pnpm --filter @workbench/ai provision-account
```

The command prompts for the account details and password, accepts only an empty identity store, and has no HTTP route. It does not include a default username or password. Run it with `--list` (also non-interactive) to show provisioned accounts without secrets; it exits 0 only when at least one account exists.

## Development workflow

Three commands cover a normal round of local work. All of them target the Electron dev database, so a provisioned account works in the app as well as on the command line.

```sh
pnpm app             # build main/preload, start Vite, launch Electron
pnpm app:dev-login   # same, but skips the login screen (developmentBypass mode + example fixtures)
pnpm app:stop        # stop Electron and the dev server
```

`pnpm app` starts one FastAPI process per Electron instance over anonymous stdio pipes. There is nothing to run by hand first; the main process spawns it, verifies it by HMAC challenge, and points it at `<userData>/workbench.db`. When the request chain misbehaves or accounts get mixed up, run `pnpm app:stop`, reset with `pnpm db:reset`, and start again.

### Accounts

```sh
pnpm account:list       # read-only table: user id, username, role, status
pnpm account:secrets    # dev only: adds password hashes and live auth sessions
pnpm account:provision  # interactive; prompts for username, display name, password
pnpm db:reset           # clear every application table, keep the schema
```

Password minimum length is 12 characters. Provisioning accepts only an empty `identities` table, so to start over run `pnpm db:reset` first, then `pnpm account:provision`. The `--show-secrets` flag prints argon2id hashes and session token ids for accounts owned by the local machine; it is opt-in development tooling, the default listing stays redacted, and existing tests enforce that (`tests/auth/test_provision_account.py`).

Every account command reads `WORKBENCH_DB` (defaults to the Electron dev database at `~/Library/Application Support/@workbench/desktop/workbench.db`) and `WORKBENCH_PYTHON` (defaults to `apps/ai/.venv/bin/python`) when you need a different database or interpreter.

### What runs where

The desktop client does not talk to a TCP port in development. Electron main spawns `app.ipc_service` from the venv and the renderer reaches FastAPI only through a trusted IPC bridge after capability verification. `python -m app.main` on port 8000 is the standalone server, useful for curl against `/auth/login` but not part of the app flow. Electron dev fails with `No module named uvicorn` if the venv is missing or broken, and its attempts, exits, and diagnostics land in `<userData>/startup.log`.

Authentication defaults come from the environment. Electron provisions and persists a signing secret under `<userData>/auth-signing-secret` on first launch, so a manual `python -m app.main` needs `WORKBENCH_APP_AUTH_SIGNING_SECRET` set or startup refuses to proceed. Reload behavior differs by surface: renderer changes hot-reload through Vite; anything in Electron main or preload needs a restart of `pnpm app`.

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
