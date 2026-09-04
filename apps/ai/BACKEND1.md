# Backend 1 — FastAPI and agent workflow

Read [AGENTS.md](../../AGENTS.md) and [PLAN.md](../../PLAN.md) before starting. They define the fixed stack, language rules, sovereignty requirements, and MVP acceptance criteria.

## Own these areas

- FastAPI application startup, configuration, route registration, and health endpoint.
- Local login, sessions, upload endpoints, chat-message endpoints, and streamed activity events.
- Shared Pydantic request/result contracts and the tool-call contract.
- The workflow controller and state transitions: upload → extract → retrieve → draft → validate → approval → export.
- Approval records and policy enforcement. The backend, not the LLM, decides whether a requested side effect is allowed.
- Tool registry: validate tool name, arguments, workflow stage, and approval requirement before delegating to an executor.

## Coordinate with the team

- Backend 1 owns `main.py`, application configuration, router registration, and shared contracts. Teammates should request changes here rather than editing them independently.
- Call AI-owned modules for model, routing, and knowledge operations. Do not implement prompts, Ollama request details, model selection logic, or RAG quality logic.
- Call Backend 2-owned modules for storage, document creation, sandbox execution, admin data, and audit persistence.

## Do not touch

- Do not modify `apps/desktop`, `apps/web`, `apps/api`, or frontend code.
- Do not introduce a Node backend, hosted service, cloud API, remote fallback, telemetry, or unapproved dependency.
- Do not make the LLM a security authority or let it execute raw shell commands.

## Done means

- Electron can call local FastAPI endpoints for login, session creation, upload, chat, activity events, and approval resolution.
- The controller rejects invalid stage changes and blocks side effects until the user approves.
- The controller can integrate the AI and Backend 2 modules through explicit Pydantic contracts.
