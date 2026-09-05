# WorkBench desktop

The employee Electron client. The renderer is built with React, TypeScript, Vite, and Tailwind CSS.

## Development

Install the workspace dependencies, then run:

```sh
pnpm --filter @workbench/desktop dev
```

`dev` builds main and preload concurrently through Vite's API, starts Vite on `127.0.0.1:5173`, and launches Electron once the server is listening. It does not spawn package-manager shell wrappers. To run the built renderer without packaging the app, run `pnpm --filter @workbench/desktop build` followed by `pnpm --filter @workbench/desktop start`. Both launch commands remove an inherited `ELECTRON_RUN_AS_NODE` flag so they work from Electron-based editors and agent terminals. `start` forces the built renderer and does not build first.

For local UI work, `WORKBENCH_SKIP_AUTH=1 pnpm --filter @workbench/desktop dev` opens the workspace without calling the employee login or session-restore endpoints. This bypass is enabled only by the development renderer, creates no FastAPI session, and grants no backend permissions. The workspace keeps a visible `Development mode: authentication disabled` notice. The flag is ignored when the development renderer is not active, including `start` and packaged builds. Leave it unset to exercise normal authentication.

The client connects to FastAPI only at the configured origin `http://127.0.0.1:8000`. The same origin is used by Electron, the renderer, and its content security policy. Set `WORKBENCH_START_LOCAL_SERVICE=1` only when `apps/ai` contains the configured FastAPI entry point and local Python environment. See `.env.example` for the local development variables.

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

These routes are not present in `apps/ai` yet. Until Backend 1 implements them, the login screen reports the unavailable endpoint or service error. It never treats a failed request as a successful sign-in. Expired session payloads are rejected, and the client revalidates when an accepted session reaches its expiry time. Sign-out clears the local UI even when FastAPI is unavailable, but it reports server revocation as unconfirmed in that case.
