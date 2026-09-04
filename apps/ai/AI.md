# AI — local models, routing, and knowledge

Read [AGENTS.md](../../AGENTS.md) and [PLAN.md](../../PLAN.md) before starting. They define the fixed stack, language rules, sovereignty requirements, and MVP acceptance criteria.

## Own these areas

- Local Ollama adapter and model-health checks.
- Qwen3 text generation for planning, drafting, structured tool requests, and code repair.
- Qwen3-VL analysis for scanned PDFs, images, OCR, and inspection findings.
- Local embedding generation, Chroma ingestion/querying, chunking strategy, retrieval evaluation, and source metadata.
- Deterministic capability routing: text/code → text model, scans/images → vision model, retrieval/indexing → embedding model.
- Prompts, structured model output schemas, extraction quality, grounded drafting, and the golden AI evaluation corpus.

## Coordinate with the team

- Expose small Python interfaces such as `generate_text`, `analyze_image`, `create_embeddings`, `search_knowledge`, and `choose_capability`.
- Backend 1 owns workflow state, tool safety, approval policy, and HTTP contracts. Your model output must be structured and validatable; it cannot bypass workflow policy.
- Backend 2 owns persistent files, artifact creation, sandbox execution, and audit storage. Use its interfaces rather than reading/writing arbitrary paths.
- Give Backend 1 stable Pydantic result models early so the workflow can be integrated in parallel.

## Do not touch

- Do not modify `apps/desktop`, `apps/web`, `apps/api`, or frontend code.
- Do not add cloud models, web search, remote APIs, model downloads at runtime, or telemetry.
- Do not make citations free-form model text. Return retrieved source IDs/page metadata so the backend can render citations.
- Do not execute tools or write files directly. Request an allowed action through Backend 1's tool/workflow contract.

## Done means

- The local text, vision, and embedding models pass health checks through Ollama.
- A scanned report and photograph yield the three expected findings.
- Local retrieval finds the correct SOP evidence and returns source IDs/page metadata.
- The draft is structured, cites retrieved evidence, marks uncertainty, and does not invent unsupported critical claims.
