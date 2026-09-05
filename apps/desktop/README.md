# WorkBench desktop

The employee Electron client. The renderer is built with React, TypeScript, Vite, and Tailwind CSS.

## Development

Install the workspace dependencies, then run:

```sh
pnpm --filter @workbench/desktop dev
```

`dev` builds main and preload concurrently through Vite's API, starts Vite on `127.0.0.1:5173`, and launches Electron once the server is listening. It does not spawn package-manager shell wrappers. To run the built renderer from this checkout, run `pnpm --filter @workbench/desktop build` followed by `pnpm --filter @workbench/desktop start`. `start` serves the built renderer on `http://127.0.0.1:5173`, the default attached FastAPI CORS origin. Both launch commands remove an inherited `ELECTRON_RUN_AS_NODE` flag so they work from Electron-based editors and agent terminals.

## Windows package

Build the installer on a Windows workstation with the approved Python environment and all `apps/ai/requirements.txt` packages installed:

```sh
pnpm --filter @workbench/desktop dist:win
```

The command builds the renderer, freezes FastAPI and its Python dependencies into `resources/service`, then creates an NSIS installer under `apps/desktop/dist`. The packaged client always starts that service. It serves the renderer at `http://127.0.0.1:5173`, passes that exact origin to FastAPI CORS, and writes both the SQLite database and persistent signing secret under Electron `userData`. It never accepts `Origin: null`.

PyInstaller produces binaries for its build platform. Build the Windows installer on Windows. The installer does not download Python, dependencies, models, Ollama, Docker, or LibreOffice at runtime.

Before the first packaged login, a local administrator must provision the initial employee into the packaged database. Electron uses `%APPDATA%\\WorkBench\\workbench.db` for the default Windows `userData` path:

```bat
set WORKBENCH_APP_DATABASE_PATH=%APPDATA%\\WorkBench\\workbench.db
pnpm --filter @workbench/ai provision-account
```

The provisioning command remains interactive and has no HTTP route. It requires a checkout with the approved Python environment. The installer does not provide a default account.

For local UI work, `WORKBENCH_SKIP_AUTH=1 pnpm --filter @workbench/desktop dev` opens the workspace without calling the employee login or session-restore endpoints. This bypass is enabled only by the development renderer, creates no FastAPI session, and grants no backend permissions. The workspace keeps a visible `Development mode: authentication disabled` notice. The flag is ignored when the development renderer is not active, including `start` and packaged builds. Leave it unset to exercise normal authentication.

The client connects to FastAPI only at `http://127.0.0.1:8000`. The same origin is used by Electron, the renderer, and its content security policy. During checkout-based development, set `WORKBENCH_START_LOCAL_SERVICE=1` to run FastAPI from `apps/ai`. Electron uses `WORKBENCH_PYTHON` first, then `apps/ai/.venv`, then Python on `PATH`; an empty override is treated as unset. Set `WORKBENCH_PYTHON` to the Python 3.11+ executable when it is not already on `PATH`. Before the first authenticated launch, provision the one local employee account from an interactive terminal:

```sh
pnpm --filter @workbench/ai provision-account
```

The command reads the password without echoing it, creates an employee only when the database has no identities, and has no HTTP equivalent. It does not ship a default account or password. See `.env.example` for the local development variables.

## Checks

```sh
pnpm --filter @workbench/desktop lint
pnpm --filter @workbench/desktop typecheck
pnpm --filter @workbench/desktop build
pnpm --filter @workbench/desktop test
```

The renderer has no Node.js access. Electron enables context isolation and Chromium sandboxing, disables Node integration, and limits network requests to the exact FastAPI origin and the development renderer origin.

## Identity contract status

The desktop client expects the local FastAPI service to provide `POST /auth/login`, `GET /auth/session`, and `POST /auth/logout`. Login and session restoration use a `{ "session": ... }` envelope with `sessionId`, `user` (`employeeId`, `username`, `displayName`, `role`), and `expiresAt` fields. Logout uses its own `{ "revoked": boolean }` response so the client can report server revocation separately from local UI sign-out. The client sends credentials with `credentials: "include"` and does not store a bearer token in the renderer.

FastAPI implements these routes with local SQLite-backed identities and revocable sessions. The login screen reports unavailable or service errors and never treats a failed request as a successful sign-in. Expired session payloads are rejected, and the client revalidates when an accepted session reaches its expiry time. Sign-out clears the local UI even when FastAPI is unavailable, but it reports server revocation as unconfirmed in that case.
