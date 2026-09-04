# WorkBench desktop

The employee Windows client. Electron owns the desktop window and native file dialogs. React, TypeScript, Vite, and Tailwind build the renderer.

## Current status

The Foundation milestone is complete. The client has the login and session presentation, workspace shell, chat and settings UI, attachment metadata selection, workflow display components, error toasts, and command palette.

FastAPI integration is not complete. The client expects `POST /auth/login`, `GET /auth/session`, and `POST /auth/logout`, but those routes are not present in `apps/ai` yet. It reports unavailable or failed service responses instead of treating them as successful. Chat threads and drafts are memory-only, the Send button is inert, and no upload, message, activity-event, model-routing, finding, citation, artifact, or sandbox route is live.

The example workflow is a development-only fixture preview. It is clearly labelled, does not contact FastAPI, and is not available in normal authenticated mode.

## Run the desktop app

Run these commands from the repository root.

```sh
pnpm install
pnpm --filter @workbench/desktop dev
WORKBENCH_SKIP_AUTH=1 pnpm --filter @workbench/desktop dev
pnpm --filter @workbench/desktop typecheck
pnpm --filter @workbench/desktop lint
pnpm --filter @workbench/desktop build
pnpm --filter @workbench/desktop start
```

`dev` runs `scripts/dev.mjs`. It builds Electron's main and preload processes, starts Vite at `http://127.0.0.1:5173`, waits for Vite, and opens Electron with the development renderer. The bypass command skips login and session restoration only in that unpackaged development renderer. It creates no FastAPI session and grants no backend permissions. `start` runs `electron .` against built files and does not enable the bypass.

The desktop `build` script is `pnpm build:electron && vite build`. `build:electron` runs the main and preload Vite configs before the renderer build. The workspace allows Electron's package build script with `allowBuilds.electron: true` in `pnpm-workspace.yaml`. pnpm needs that narrow approval to install Electron's binary. It is not a general permission for application shell commands. The workspace separately allows the transitive `esbuild` build script.

For PowerShell, set the bypass for the command's process with:

```powershell
$env:WORKBENCH_SKIP_AUTH="1"; pnpm --filter @workbench/desktop dev
```

## Local service

The client contacts only `http://127.0.0.1:8000`. Without `WORKBENCH_START_LOCAL_SERVICE=1`, Electron uses attached-service mode and does not start FastAPI.

Set `WORKBENCH_START_LOCAL_SERVICE=1` only when `apps/ai` has its local Python environment and `app.main:app` entry point. Electron runs the following from `apps/ai`:

```text
macOS/Linux: python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Windows:     python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

It passes `WORKBENCH_LOCAL_ONLY=1` to the child and attempts to restart it after an unexpected exit. The current checkout does not include `apps/ai/app/main.py`, so attached mode is the supported default until the backend supplies that entry point.

`scripts/dev.mjs` sets `WORKBENCH_DEV_RENDERER=1` and `VITE_DEV_SERVER_URL` for the development renderer. `WORKBENCH_SKIP_AUTH` is ignored unless Electron is unpackaged and the development renderer is active. `.env.example` documents the flags; it does not load them. Set them in the shell before running the command.

## Implemented UI

- Login and session restoration states, field validation, expiry revalidation, local sign-out, and service-failure messages.
- Development bypass notice with isolated fixture access.
- Collapsible title bar and sidebar with chat search, recent chats, and new chat.
- Chat switching, in-memory drafts, inspection report and site photograph selection, and metadata-only generic attachments.
- Settings sections for general, local service, and security status, plus the account popover.
- Plain-text message, activity trace, workflow stage, upload progress, failed or cancelled workflow notice, structured finding, and application-controlled citation components.
- Recoverable service-error toasts with retry actions and a searchable command palette.

## Dependency summary

The declared versions come from `apps/desktop/package.json`.

- Electron: `electron ^40.0.0`, for the Windows shell, main process, preload boundary, native dialogs, and optional local-service lifecycle.
- React: `react ^19.2.8` and `react-dom ^19.2.8`, for the renderer UI.
- TypeScript: `typescript ^6.0.3`, for strict process, IPC, API, and UI contracts.
- Vite: `vite ^8.2.2` and `@vitejs/plugin-react ^6.1.1`, for the three build entry points and React support.
- Tailwind: `tailwindcss ^4.3.3` and `@tailwindcss/vite ^4.3.3`, for renderer styling and Vite integration.
- shadcn source components: local source under `src/renderer/components/ui`, configured by `components.json` with style `new-york`; there is no shadcn runtime package.
- Radix primitives: `@radix-ui/react-label ^2.1.15`, `@radix-ui/react-popover ^1.1.23`, and `@radix-ui/react-tooltip ^1.2.16`, for accessible form labels, popovers, and tooltips.
- Lucide: `lucide-react ^1.40.0`, for interface icons.
- Sonner: `sonner ^2.0.8`, for recoverable error toasts.
- Motion: `motion ^13.2.0`, for toast animation with reduced-motion support.
- cmdk: `cmdk ^1.1.1`, for the searchable command palette.
- Class utilities: `class-variance-authority ^0.7.1`, `clsx ^2.1.1`, and `tailwind-merge ^3.6.0`, for typed variants and composed Tailwind class names.

The full dependency and milestone record is in [`PLAN.md`](./PLAN.md).

## Security boundaries and limitations

- The renderer receives only typed preload methods. It cannot access Ollama, Chroma, SQLite, Docker, host files, or child processes.
- Native selection validates file type, regular-file status, and size in the main process. The renderer preview contract contains names, MIME types, input kind where applicable, and sizes only. It contains no file bytes or source paths.
- Messages render as plain text. Citations must come from structured application data, not model-written citation text.
- The development bypass changes presentation access only. It does not create a backend session or grant backend permissions. There are no production fixtures and no fake live results.
- Artifact save/open IPC, export, sandbox execution, remote fallbacks, and backend workflow routes are intentionally absent until their contracts and policy checks are implemented.

See [`PLAN.md`](./PLAN.md) for the milestone checklist and the root [`PLAN.md`](../../PLAN.md) for the repository source of truth.
