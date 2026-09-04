# WorkBench MVP execution board

## Outcome

Deliver a demonstrable, air-gapped AI workbench by 8 September. It must run entirely on one Windows workstation and turn confidential industrial documents into locally grounded, approval-gated deliverables without external services.

## Locked architecture

```text
Electron employee client
        |
   localhost FastAPI
   |       |        |
Ollama   Chroma   SQLite + local files
   |
Qwen3 / Qwen3-VL / embedding model
```

- Electron is the Windows employee client; FastAPI runs the local workflow and admin API.
- Ollama serves preloaded Qwen3 4B for planning, drafting, tools, and code; Qwen3-VL 4B for scanned-PDF/image OCR and visual findings; and a local embedding model for retrieval. Larger models are benchmarked configuration upgrades, never hard-coded requirements.
- Chroma persists the curated SOP/template corpus and page/section metadata locally. Disable telemetry. SQLite stores pre-seeded employee/operator accounts, sessions, approvals, and audit metadata. Local folders hold uploads, temporary workspaces, and generated artifacts.
- The agent is hybrid: deterministic rules select only eligible models/tools and enforce upload → extract → retrieve → draft → validate → approval → export. The planner acts only inside those constraints.
- The operator uses a separate localhost admin page for model configuration, curated-corpus ingestion, service health, and audit/network status. Keep this page deliberately minimal.
- Docker executes the coding task with no network, resource limits, and only a temporary task-folder mount.
- The static web app is a mock product/install landing page with no package download, account system, or remote dependency.

## MVP workstreams

1. **Desktop and identity** — Electron chat UI, local employee/operator login, guided “Analyze inspection report” starter, upload flow, activity trace, approval cards, and Security Status box.
2. **Local AI workflow** — FastAPI session API, local Ollama adapter, deterministic capability router, planner loop, and structured tool results. Read direct uploads and curated corpus automatically; prompt before code execution or artifact side effects.
3. **Multimodal and knowledge** — Render scanned PDF pages locally; route scans/photos to Qwen3-VL; ingest four sanitized documents (report, SOP, prior note, approval-note template) plus a photograph. Chunk with source/page/section metadata; embed and retrieve locally through Chroma.
4. **Deliverables** — Validate findings and source support; render application-controlled citations; create editable DOCX, then locally convert an approved draft to PDF. Test local PDF conversion early.
5. **Sandbox and proof** — Generate a small inspection-data validator, run it in the isolated sandbox against flawed input, repair it, and rerun it successfully. Display Air-gapped mode, local inference, external APIs `0`, current model, and outbound status; show a terminal/OS network check in the demo.

## Acceptance checklist

- A scanned report and photograph route to the local VLM and yield the three predetermined findings.
- The text model retrieves the correct SOP pages, drafts an approval note, and uses only backend-rendered source citations.
- The user must approve DOCX/PDF creation; the resulting files open and contain required note sections, findings, citations, and uncertainty/missing-data handling.
- The coding task runs live in a network-disabled sandbox, detects a deliberate failure, and ends with a passing rerun.
- Security Status and a local network check demonstrate zero external API use/connections.
- A smoke test covers routing, extraction, retrieval metadata, artifact creation, sandbox execution, and absence of cloud configuration. Rehearse the golden path twice on the actual demo machine.
- If capacity or model quality is constrained, use a tested smaller local model. Never present precomputed output as a live agent result.

## Golden demo

An inspection engineer uploads a scanned inspection report and site photograph. WorkBench selects the VLM, extracts findings, retrieves the applicable local SOP and prior-note context, drafts a cited approval note, requests export approval, and generates DOCX/PDF. A short second task demonstrates the isolated code-repair cycle. The product remains live; the dataset and expected findings are curated for repeatability.

## Future scope

- Validate Jetson Orin and internal-LAN multi-user GPU-server deployment.
- Add TLS, VPN, SSO, MFA, RBAC, departmental policy, quotas, and richer audit analytics.
- Add approved model-install flows, evaluation pipelines, a richer registry, shared-drive/ERP/DMS connectors, and multi-server scaling.
- Add advanced P&ID/CAD understanding, engineering-calculation workflows, Hindi/regional language support, and broader Word/Excel/PPT generation.
