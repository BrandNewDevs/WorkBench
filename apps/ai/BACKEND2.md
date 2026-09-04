# Backend 2 — local tools, storage, and proof

Read [AGENTS.md](../../AGENTS.md) and [PLAN.md](../../PLAN.md) before starting. They define the fixed stack, language rules, sovereignty requirements, and MVP acceptance criteria.

## Own these areas

- SQLite metadata for users, sessions, approvals, artifacts, and audit records.
- Local session workspaces: uploads, extracted material, temporary task files, generated outputs, and cleanup.
- Curated-corpus file ingestion storage for SOPs, templates, and approved reference material.
- DOCX generation from validated structured drafts and local PDF conversion after approval.
- Docker sandbox execution: temporary mount only, no network, resource limits, timeout, captured stdout/stderr/exit code.
- Minimal local admin data and health/audit/network-status information for the operator page.

## Coordinate with the team

- Expose narrow Python interfaces such as `create_artifact`, `run_sandbox`, `save_session_file`, and `record_audit_event`.
- Backend 1 owns HTTP routes, shared contracts, the agent workflow, and approval decisions. Your modules execute approved work and return structured results.
- AI owns model inference, RAG retrieval quality, routing, and prompts. Store corpus files safely, then hand document material to AI ingestion/retrieval interfaces.

## Do not touch

- Do not modify `apps/desktop`, `apps/web`, `apps/api`, or frontend code.
- Do not build an arbitrary host-shell executor. Code runs only inside the network-disabled Docker sandbox.
- Do not add cloud storage, telemetry, remote conversion APIs, or runtime downloads.
- Do not change FastAPI startup, route registration, or shared contracts without Backend 1 coordination.

## Done means

- Session files and artifacts remain local, have clear ownership, and can be cleaned up.
- The document tool creates a real DOCX and local PDF after approval.
- The sandbox runs the golden code-repair task with network disabled and returns verifiable results.
- The admin/health data can prove local operation and show audit status.
