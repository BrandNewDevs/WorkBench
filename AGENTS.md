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

## Technology baseline

- Build the employee desktop client with **Electron + React + TypeScript**. Use the existing Vite/Tailwind toolchain for renderer UI and the static product site.
- Build local services with **Python + FastAPI**. Keep workflow control, OCR/VLM integration, retrieval, document generation, and sandbox coordination in Python.
- Use **Ollama** for every local inference and embedding request; use **Chroma** for the persistent local vector index, **SQLite** for application metadata, and **Docker** for isolated code execution.
- Stay inside this stack for the MVP. A new framework, database, hosted service, model runtime, or package boundary needs an explicit `PLAN.md` revision before it is introduced.

## Language and code guidelines

- Write Electron, React, shared client types, and static-site code in TypeScript. Keep TypeScript strict; define interfaces at process/API boundaries and avoid `any`.
- Write FastAPI, orchestration, retrieval, document, and sandbox code in Python. Use type hints, Pydantic models at API boundaries, `pathlib` for filesystem paths, and `snake_case` for Python names.
- Keep API contracts explicit JSON. Use `camelCase` for JSON/TypeScript fields and translate at the Python boundary when needed; do not pass untyped dictionaries across application boundaries.
- Keep React components focused on rendering and user interaction. Put Electron main-process work behind narrow IPC interfaces and keep security-sensitive filesystem/process operations out of the renderer.
- Write UI text in clear, professional English suitable for government and industrial users. Label generated artifacts as drafts until the user approves export, use exact action language in approval prompts, and state uncertainty instead of inventing a conclusion.

## Repository boundaries

- `apps/desktop` owns the Electron employee client.
- `apps/web` owns the static product/install mock site.
- `apps/ai` owns FastAPI, local-model integration, workflow orchestration, retrieval, document processing, and sandbox controls.
- `apps/api` remains unused unless a separate server boundary becomes necessary after the MVP.
- Put only genuinely shared, framework-neutral code in `packages/`; applications must not import one another.

### Python package layout

- Treat `apps/ai` as the Python service root, not as an importable package. It contains configuration, tests, documentation, and the `app` source package.
- Treat `apps/ai/app` as the import root. Keep AI/LLM code in `apps/ai/app/ai`; place future FastAPI, workflow, storage, and sandbox packages beside `ai` under `apps/ai/app` after their owners agree on the package names.
- Use absolute service imports such as `from app.ai.schemas import Finding`. Keep `apps/ai/app/__init__.py` and an `__init__.py` in every importable subpackage. Avoid `from ai...` and `from apps.ai...`; those forms depend on an incorrect or ambiguous Python path.
- Run direct Python, Ruff, mypy, pytest, and Uvicorn commands with `apps/ai` as the working directory. Repository commands such as `pnpm --filter @workbench/ai test` already use that workspace. When Electron launches the Python service, set its child-process working directory to `apps/ai`; an explicit Uvicorn `--app-dir apps/ai` is the equivalent when launching from the repository root.
- Mirror AI tests under `apps/ai/tests/ai`. Never patch `sys.path` inside application or test files; keep the import root in project/tool configuration.

## Validate changes

- Run `pnpm check` for repository-level TypeScript/lint validation.
- Exercise the relevant end-to-end MVP acceptance path in `PLAN.md`; a screen existing is not proof that the workflow works.
- Do not substitute prepared output for a failed live model/tool result. The allowed demo fallback is a tested smaller local model.
