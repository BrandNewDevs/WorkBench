# Desktop MVP plan

This file defines the employee desktop work for the MVP. The repository-root `PLAN.md` remains the source of truth if the two documents conflict.

## Goals

Build a Windows Electron client that demonstrates the WorkBench golden path on one workstation:

1. Sign in with a local employee account.
2. Upload a scanned inspection report and site photograph.
3. Follow extraction, retrieval, drafting, validation, and approval activity.
4. Review grounded findings and application-controlled citations.
5. After the backend approval contract is fixed, approve and save draft DOCX and PDF artifacts.
6. Approve and observe the isolated code-repair task.
7. Verify local inference and zero external API use in the Security Status view.

The client must stay usable when a local service is slow or unavailable. It must never replace a failed live operation with prepared output. It must clear local authentication state on sign-out even when server revocation cannot be confirmed.

## Current state

FastAPI route names, JSON schemas, and approval contracts are not fixed yet. The desktop client now defines the pending frontend contract for employee login, session restoration, and sign-out, with the expected paths isolated in `src/renderer/api/localApi.ts`. No backend route was added. Until Backend 1 implements and verifies those routes, the client shows the returned unavailable or error state rather than treating sign-in as successful. It rejects expired session payloads and revalidates an accepted session at expiry. The desktop bridge intentionally has no artifact-save capability until the backend can verify approval server-side.

## MVP scope

- Electron application shell for Windows.
- React, TypeScript, Vite, and Tailwind renderer.
- Local employee login and session restoration.
- Guided "Analyze inspection report" starter.
- Native selection of report and image uploads.
- Chat and workflow activity display.
- Structured findings, uncertainty, and citation display.
- Approval cards with exact action and target details.
- Reserve the draft artifact list and local save/open actions for the agreed approval contract.
- Coding-task approval, execution output, failure, repair, and passing rerun.
- Security Status showing air-gapped mode, local inference, current model, external APIs `0`, outbound status, and service health.
- Clear loading, cancellation, rejection, stale-status, service-failure, and retry states.

## Architecture boundaries

### Electron main process

The main process owns window creation, local service lifecycle, and native file dialogs. Artifact save/open operations remain disabled until the backend approval contract is agreed and verified. It must use direct process arguments with `shell: false` and must not expose general filesystem, process, or shell access.

For repository development, a FastAPI child process must use `apps/ai` as its working directory. The client may attach to an already running configured loopback service during development.

### Preload bridge

A context-isolated preload script exposes a narrow, typed `DesktopBridge`. The bridge currently permits only selecting upload files and reading desktop service status. Artifact operations stay out of IPC until the backend approval contract is agreed.

Do not enable Node integration in the renderer. Keep Chromium sandboxing enabled.

### Renderer

The renderer owns presentation and authenticated user interaction. It communicates only with the preload bridge and the configured `localhost` FastAPI API.

The renderer does not access Ollama, Chroma, SQLite, Docker, host files, or child processes. It does not decide routing eligibility, workflow transitions, approval validity, or tool permissions.

Treat model output and document text as untrusted content. Render citations only from structured source metadata returned by FastAPI. Do not interpret model-written citation text as evidence.

### API contracts

Define all request, response, activity-event, approval, artifact, citation, and health payloads as strict TypeScript interfaces using `camelCase`. Keep them aligned with backend Pydantic contracts. Do not use `any` or pass untyped dictionaries through IPC.

Suggested target files and symbols:

- `src/main/index.ts`: `createMainWindow`, `startLocalService`, `stopLocalService`
- `src/main/ipc.ts`: `registerDesktopIpc`
- `src/preload/index.ts`: expose `window.workbench`
- `src/shared/contracts.ts`: `DesktopBridge` and IPC payloads
- `src/renderer/api/localApi.ts`: `LocalApiClient`
- `src/renderer/App.tsx`: application shell and route-level state
- `src/renderer/components/ApprovalCard.tsx`
- `src/renderer/components/ActivityTrace.tsx`
- `src/renderer/components/CitationList.tsx`
- `src/renderer/components/SecurityStatus.tsx`

## Milestones

### 1. Foundation

- Replace placeholder scripts with Electron, Vite, lint, typecheck, and build commands.
- Add the secure main, preload, shared-contract, and renderer structure.
- Open the application on the target Windows workstation and report FastAPI health failures clearly.

### 2. Identity and workflow

- [in progress] Agree on backend contracts for login, sessions, uploads, messages, and activity events. The desktop login and session shapes are typed, but the FastAPI routes are not present yet.
- [desktop items 1 and 2 complete] Implement the local employee login screen, authenticated session state, session restoration attempt, expiry revalidation, and sign-out. Unavailable, unauthorized, malformed-response, timeout, and network failures remain visible to the employee.
- Show live workflow stages and local model routing without exposing internal reasoning.

### 3. Grounded draft and artifacts

- Render structured findings, uncertainty, and backend-provided citations.
- Agree on the backend approval and artifact contracts before adding artifact creation, export, save, or open flows.
- Mark generated artifacts as drafts until the user approves export.
- Add real DOCX and PDF operations only after the backend verifies the approval.

### 4. Sandbox and security proof

- Add approval UI for sandbox execution.
- Display captured output, deliberate failure, repair, and passing rerun.
- Add Security Status using backend health and audit data. Show stale or unavailable data as unknown, never secure.

### 5. Windows hardening and rehearsal

- Test service startup and shutdown, refresh, restart, rejected approvals, missing files, backend loss, and malformed events.
- Run repository checks and the complete golden path twice on the demo workstation.
- Produce a repeatable Windows build. Installer, signing, and automatic updates require separate scope approval.

## Acceptance criteria

- `pnpm check` and the desktop production build pass with strict TypeScript and no placeholder scripts.
- The Electron renderer runs with context isolation and sandboxing enabled, with Node integration disabled.
- A local employee can sign in, start "Analyze inspection report", and upload the curated scanned report and photograph.
- The activity trace shows local VLM routing and the resulting expected findings.
- The draft shows the correct SOP source metadata. The renderer uses only backend-provided citation fields.
- No artifact creation, export, save, or sandbox request reaches FastAPI before explicit approval. Rejection causes no side effect.
- Artifact save and open remain unavailable until the backend approval contract is implemented and verified. Once enabled, approved DOCX and PDF files must save locally, open successfully, contain the required sections, and remain labeled as drafts until export approval.
- The coding task displays the failed run and successful repaired rerun from the live network-disabled sandbox.
- Security Status shows the current local model, local inference, external APIs `0`, outbound status, and failures or stale data without making an unsupported security claim.
- Loss of FastAPI produces a recoverable error rather than a blank screen, fabricated result, or remote fallback.
- The complete root-plan golden path succeeds twice on the target Windows workstation.

## Out of scope

- The localhost admin page and curated-corpus administration.
- AI prompts, model routing, retrieval, OCR, document generation, storage, workflow enforcement, and Docker implementation.
- Direct access from Electron to Ollama, Chroma, SQLite, or Docker.
- Cloud APIs, telemetry, CDN assets, remote fallbacks, and runtime model downloads.
- LAN or multi-user deployment, Jetson support, SSO, MFA, RBAC, and enterprise connectors.
- General host-shell execution or unrestricted filesystem browsing.
- A public download service, automatic updater, signed installer, or app-store release.
- Workflows beyond the inspection-note and code-repair golden paths unless the root `PLAN.md` changes.
