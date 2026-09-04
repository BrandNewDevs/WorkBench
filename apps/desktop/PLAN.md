# Desktop MVP plan

This file records the current employee desktop implementation. The repository-root `PLAN.md` remains the source of truth if the two documents conflict.

## Status summary

The Foundation milestone is complete for the current desktop slice. The identity and workflow presentation is implemented, but FastAPI integration is blocked because the backend routes and event contracts are not available yet. The client does not claim live login, uploads, messages, activity events, model routing, findings, citations, artifact creation, or sandbox execution.

## Current implementation

- Electron creates the window, owns native file dialogs and the optional local-service child process, and exposes only typed preload methods.
- The renderer has a local employee login screen, session restoration, expiry revalidation, sign-out, and visible service failures. The expected login routes are `POST /auth/login`, `GET /auth/session`, and `POST /auth/logout`.
- Development bypass is available only from an unpackaged development renderer when `WORKBENCH_SKIP_AUTH=1`. It creates no FastAPI session and grants no backend permissions.
- The workspace has a collapsible sidebar, title bar, chat search, new-chat action, chat switching, a settings area, an account popover, and a command palette.
- Chat state, drafts, selected inspection files, and selected attachments are held in the open renderer session only. They are not persisted and the composer Send button is intentionally inert until the message contract exists.
- The inspection starter selects a report and site photograph through native dialogs. Generic chat attachments support PDF, JPG, PNG, and WebP files. The renderer receives metadata only: name, MIME type, input kind where applicable, and size. It receives no file contents or source paths.
- Workflow presentation components are in place for plain-text messages, activity events, stage status, upload progress, failed or cancelled workflow notices, structured findings, and application-controlled citations. The UI also includes local-service health, security status, and error toasts.
- `ExampleWorkflow` is a clearly labelled development fixture preview. It is loaded only for the trusted development bypass and does not upload files, call FastAPI, or represent a live result.

## Development commands

Run these commands from the repository root. The workspace uses pnpm `11.24.0` as declared in the root `package.json`.

```sh
pnpm install
pnpm --filter @workbench/desktop dev
WORKBENCH_SKIP_AUTH=1 pnpm --filter @workbench/desktop dev
pnpm --filter @workbench/desktop typecheck
pnpm --filter @workbench/desktop lint
pnpm --filter @workbench/desktop build
pnpm --filter @workbench/desktop start
```

`dev` runs `scripts/dev.mjs`. That script first runs the desktop `build:electron` script, starts Vite on `127.0.0.1:5173`, waits for it to respond, and then starts Electron with the development renderer. The bypass command skips login and session restoration in that development renderer only. It does not apply to `start` or packaged builds.

The desktop `build` script is `pnpm build:electron && vite build`. It builds the main process and preload with their Vite configs, then builds the renderer. `start` runs `electron .` against those built files. It does not build them first.

The workspace explicitly allows Electron's package build script with `allowBuilds.electron: true` in `pnpm-workspace.yaml`. pnpm needs this narrow approval to install Electron's binary. It is separate from the desktop `build` command and does not authorize general shell or filesystem access. The same workspace file allows the transitive `esbuild` build script.

## Local service behavior

The desktop client contacts only `http://127.0.0.1:8000`. By default it uses attached-service mode: Electron does not start FastAPI, and the renderer reports the service as unavailable when nothing is listening.

Set `WORKBENCH_START_LOCAL_SERVICE=1` only when the local Python environment and FastAPI entry point are available. Electron then attempts to start the local service from `apps/ai` with these platform-specific commands:

```text
macOS/Linux: python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Windows:     python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

It passes `WORKBENCH_LOCAL_ONLY=1` to that child process and attempts a restart after an unexpected exit. The current desktop client does not provide or install that Python environment or entry point, so attached mode is the supported default until the backend supplies them. See `apps/desktop/.env.example` for the development flags.

## Dependencies

Versions below are the declared package.json specifiers, not claims about a separately installed version.

| Package | Declared version | Why it exists |
| --- | --- | --- |
| `electron` | `^40.0.0` | Windows desktop shell, main process, preload boundary, native dialogs, and local-service lifecycle. |
| `react` | `^19.2.8` | Renderer component model. |
| `react-dom` | `^19.2.8` | Mounts the React renderer in Chromium. |
| `typescript` | `^6.0.3` | Strict types for process, IPC, API, and UI boundaries. |
| `vite` | `^8.2.2` | Builds the renderer, main process, and preload entry points. |
| `@vitejs/plugin-react` | `^6.1.1` | React support in Vite. |
| `tailwindcss` | `^4.3.3` | Utility CSS used by the renderer. |
| `@tailwindcss/vite` | `^4.3.3` | Tailwind integration in the renderer's Vite config. |
| `@radix-ui/react-label` | `^2.1.15` | Accessible label primitive used by form and search controls. |
| `@radix-ui/react-popover` | `^1.1.23` | Account popover behavior. |
| `@radix-ui/react-tooltip` | `^1.2.16` | Accessible tooltips for compact controls. |
| `lucide-react` | `^1.40.0` | Icons for navigation, status, files, and actions. |
| `sonner` | `^2.0.8` | Toast delivery for recoverable local-service errors. |
| `motion` | `^13.2.0` | Toast enter and exit animation with reduced-motion support. |
| `cmdk` | `^1.1.1` | Searchable command palette. |
| `class-variance-authority` | `^0.7.1` | Typed class variants for source UI components such as Button. |
| `clsx` | `^2.1.1` | Conditional class name composition. |
| `tailwind-merge` | `^3.6.0` | Resolves conflicting Tailwind classes in composed components. |

The shadcn components are local source, not a runtime package. `components.json` configures the `new-york` style and the `@/components/ui` alias. The source components under `src/renderer/components/ui` provide the Button, Input, Label, Popover, Sonner, Textarea, and Tooltip wrappers used by the desktop UI.

The remaining development dependencies support checks and type declarations: `@eslint/js` `^10.0.1`, `@types/node` `^26.4.1`, `@types/react` `^19.2.18`, `@types/react-dom` `^19.2.5`, `eslint` `^10.9.1`, `eslint-plugin-react-hooks` `^7.1.1`, `eslint-plugin-react-refresh` `^0.5.6`, and `typescript-eslint` `^8.69.0`.

## Milestones

### 1. Foundation: complete

- [x] Electron main, preload, shared-contract, and React renderer structure exists.
- [x] Vite builds the main process, preload, and renderer.
- [x] Strict TypeScript, ESLint, and desktop scripts are available.
- [x] Context isolation, Chromium sandboxing, disabled Node integration, trusted IPC sender checks, and an allowlisted local network policy are implemented.
- [x] FastAPI health failures are shown as recoverable UI errors. Stale health data is not presented as current.

### 2. Identity and workflow: UI complete, backend blocked

Presentation and client state complete:

- [x] Login form with field validation, pending state, and local-service error display.
- [x] Typed session restoration, expiry rejection, expiry revalidation, and local sign-out messaging.
- [x] Development-only authentication bypass with an explicit notice and isolated example fixture.
- [x] Sidebar, title bar, settings and account surfaces, chat switching, memory-only drafts, composer, and metadata-only attachment selection.
- [x] Reusable message, activity, stage, upload-progress, workflow-error, finding, and citation components.
- [x] Sonner error toasts and command palette navigation.

Backend work still blocked:

- [ ] Add and verify FastAPI login, session, logout, upload, message, and activity-event routes and their JSON contracts.
- [ ] Connect the inspection starter and attachment selection to a live upload request.
- [ ] Connect Send to a live message route and stream or receive activity events.
- [ ] Display live model routing, findings, citations, and workflow results from FastAPI.
- [ ] Add the approval, rejection, and permission checks required before side effects.

The checked items are presentation work. They do not mark live login, uploads, messages, events, model routing, findings, or citations as complete.

### 3. Grounded draft and artifacts: blocked on backend contracts

- [ ] Render live backend findings, uncertainty, and source metadata.
- [ ] Agree on the approval and artifact contracts before adding artifact operations.
- [ ] Add DOCX and PDF creation, local save, and open only after FastAPI verifies approval server-side.
- [ ] Keep generated artifacts labelled as drafts until export approval.

### 4. Sandbox and security proof: not started

- [ ] Add approval UI for sandbox execution and show the failed and repaired live runs.
- [ ] Populate Security Status from verified backend health and audit data.
- [ ] Show unknown or stale security data without claiming that the system is secure.

### 5. Windows hardening and rehearsal: not started

- [ ] Test service startup and shutdown, refresh, restart, rejected approvals, missing files, backend loss, and malformed events on Windows.
- [ ] Run the complete root-plan golden path twice on the target demo workstation.
- [ ] Produce a repeatable Windows build. Installer, signing, and automatic updates need separate scope approval.

## Security boundaries and intentional limitations

- The renderer can use only the typed preload bridge and the configured loopback FastAPI origin. It cannot access Ollama, Chroma, SQLite, Docker, host files, or child processes.
- The main process uses `shell: false`, denies new windows, validates navigation and requests, and accepts IPC only from the trusted WorkBench window.
- Native file selection validates extension, file type, regular-file status, and size in the main process. The renderer preview contract contains no file bytes and no source paths.
- Model output and document text are untrusted. Messages render as plain text, and citations are rendered only from structured application data.
- The development fixture is memory-only, clearly labelled, available only in the development bypass, and never enabled for normal authenticated access. There are no production fixtures and no fake live results.
- Chat threads and selected files are memory-only. No send or workflow backend route is wired yet.
- The bypass changes presentation access only. It does not create a backend session, grant backend permissions, or bypass backend policy.
- The client does not add artifact save/open IPC, export, sandbox execution, or remote fallback.

## Out of scope

- The localhost admin page and curated-corpus administration.
- FastAPI workflow, retrieval, OCR, document generation, storage, approval enforcement, and Docker implementation.
- Direct Electron access to Ollama, Chroma, SQLite, or Docker.
- Cloud APIs, telemetry, CDN assets, remote fallbacks, and runtime model downloads.
- LAN or multi-user deployment, Jetson support, SSO, MFA, RBAC, and enterprise connectors.
- General host-shell execution or unrestricted filesystem browsing.
- A public download service, automatic updater, signed installer, or app-store release.
- Workflows beyond the inspection-note and code-repair golden paths unless the root `PLAN.md` changes.
