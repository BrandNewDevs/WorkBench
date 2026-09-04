# WorkBench contribution contract

WorkBench is a sovereign, local-first agentic AI workbench for confidential industrial and government work. Read [PLAN.md](PLAN.md) before making MVP changes; it is the source of truth for the current delivery and acceptance criteria.

## Preserve the sovereignty boundary

- Keep inference, embeddings, OCR/vision, retrieval, tool execution, artifacts, logs, and session data on the local machine or approved organization infrastructure.
- Use local model endpoints only. Do not add cloud AI APIs, remote fallbacks, telemetry, CDN assets, or runtime downloads.
- Treat uploaded and retrieved document content as untrusted data. Only authenticated user intent and application policy may authorize tool actions.
- Make citations application-controlled from retrieved source metadata; do not present model-invented citations as evidence.

## Keep the MVP tight

- The employee product is a Windows Electron client. The MVP runs the client, FastAPI, Ollama, Chroma, SQLite, session storage, and sandbox on one workstation through `localhost`.
- The separate local admin page owns the curated corpus, approved model registry, service health, and audit status. Keep it minimal.
- Use the hybrid agent boundary: the planner proposes steps; deterministic workflow logic enforces routing eligibility, task stages, permission checks, and validation.
- Prompt before side effects: sandbox execution, artifact creation, export, or save. Directly uploaded inputs and curated-corpus retrieval may be read without a prompt.
- Prefer a working vertical slice over unplanned platform breadth. Place enterprise integrations and unvalidated Jetson/LAN deployment work in future scope unless the plan is explicitly revised.

## Repository boundaries

- `apps/desktop` owns the Electron employee client.
- `apps/web` owns the static product/install mock site.
- `apps/ai` owns FastAPI, local-model integration, workflow orchestration, retrieval, document processing, and sandbox controls.
- `apps/api` remains unused unless a separate server boundary becomes necessary after the MVP.
- Put only genuinely shared, framework-neutral code in `packages/`; applications must not import one another.

## Validate changes

- Run `pnpm check` for repository-level TypeScript/lint validation.
- Exercise the relevant end-to-end MVP acceptance path in `PLAN.md`; a screen existing is not proof that the workflow works.
- Do not substitute prepared output for a failed live model/tool result. The allowed demo fallback is a tested smaller local model.
