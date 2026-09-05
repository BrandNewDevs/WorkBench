# WorkBench review rules

Review for correctness, security, regressions, and maintainability. Avoid subjective
style comments and praise. Report an issue only when it is actionable and supported
by the changed code and repository context.

## Architecture and sovereignty

- The MVP is a Windows Electron client talking only to localhost FastAPI, Ollama,
  Chroma, SQLite, local files, and a Docker sandbox on one workstation.
- Do not permit cloud AI/embedding/OCR APIs, hosted fallbacks, telemetry, analytics,
  remote runtime assets, CDNs, or model downloads during application execution.
- The planner may propose work, but deterministic workflow logic must enforce model
  eligibility, task stages, permissions, and validation.
- Application-controlled source metadata must produce citations. Model-invented
  citations are not evidence.

## Side effects and untrusted inputs

- Uploaded and retrieved document content, model output, and tool output are
  untrusted. They must never directly authorize an action.
- Reading a direct upload or curated corpus is allowed; sandbox execution, artifact
  creation, export, and save require an explicit approval gate.
- Flag path traversal, command injection, prompt injection, SSRF, unsafe
  deserialization, arbitrary file access, secret exposure, and failures that bypass
  approval or validation instead of failing closed.

## Repository conventions

- TypeScript is strict. Require explicit interfaces at process and API boundaries;
  do not accept `any` at those boundaries.
- Python lives under `apps/ai/app` and uses absolute imports beginning with `app.`.
  Do not accept `from ai...`, `from apps.ai...`, or `sys.path` manipulation.
- Python API boundaries use typed Pydantic models, `pathlib` for filesystem paths,
  and snake_case Python names. JSON and TypeScript contracts use camelCase.
- Keep Electron main-process operations behind narrow IPC interfaces. Renderer code
  must not gain direct filesystem, process, shell, or child-process access.
- `apps/api` is reserved and should remain unimplemented unless `PLAN.md` explicitly
  changes the architecture.

## Validation

- Required repository checks are `pnpm check`, `pnpm test`, and `pnpm build` (or
  `pnpm verify`). Python checks run through the `@workbench/ai` workspace and use
  Ruff, mypy, and offline pytest. Do not require a live local model in ordinary CI.
- Security-sensitive changes need meaningful negative-path coverage where practical,
  especially approval gates, untrusted input, local-model unavailability, sandbox
  failures, invalid paths, and citation validation.
