# Backend-facing AI engine

`AIEngine` is the only AI interface Backend 1 needs to learn. It hides model health,
deterministic routing, vision/OCR, local knowledge ingestion and retrieval, structured drafting,
tool proposals, and code repair.

The AI module does not own FastAPI routes, workflow transitions, upload approval, persistence,
artifact rendering, sandbox execution, or delivery of progress events.

## Runtime composition

Backend 1 should call the factory once from its future composition root, after Backend 2 supplies
the approved local Chroma directory. Do not call the factory inside a route or workflow operation.

```python
from app.ai.engine import AIEngine
from app.ai.local_engine import create_local_ai_engine
from app.ai.schemas import ApprovedKnowledgeRoot

local_engine = create_local_ai_engine(
    knowledge_root=ApprovedKnowledgeRoot(path=approved_chroma_directory),
)
engine: AIEngine = local_engine

try:
    health = await engine.health()
finally:
    await local_engine.close()
```

The factory creates one Ollama adapter and one persistent Chroma client. Every engine operation
reuses them. Construction does not contact Ollama or download a model; `health()` performs the
first local runtime check.

`close()` belongs to `LocalAIEngine` lifecycle ownership rather than the minimal `AIEngine`
operation interface. The future application lifespan should retain the concrete engine as its
owned resource while injecting it into callers as `AIEngine`.

## Backend tests

Backend tests should inject `FakeAIEngine`. The fake can return a fallback decision or raise a
typed AI error without Ollama, Chroma, or filesystem access.

```python
from app.ai.errors import NoRelevantEvidence
from app.ai.fakes import FakeAIEngine
from app.ai.schemas import Capability, CapabilityDecision

fallback_engine = FakeAIEngine(
    capability_decision=CapabilityDecision(
        capability=Capability.VISION,
        selected_model="qwen3-vl:2b",
        reason="The preferred local vision model is unavailable.",
        used_fallback=True,
        fallback_reason="qwen3-vl:4b could not be loaded.",
    )
)

no_evidence_engine = FakeAIEngine(
    failures={
        "search_knowledge": NoRelevantEvidence("No relevant local evidence was found."),
    }
)
```

For a valid tool-proposal success path, provide an `AgentProposal` through
`action_proposal`. The fake checks the proposal against the `allowed_tools` supplied in that
specific `AgentContext`. To test rejection, configure `InvalidToolProposal` in `failures`.

## Ownership rules for integration

- Backend 1 supplies task facts and allowed tool schemas, advances workflow stages, translates
  typed AI errors, and decides how progress reaches Electron.
- Backend 2 resolves exact approved paths and storage roots, persists validated drafts, assigns
  draft IDs, renders approved artifacts, and runs approved sandbox requests.
- The AI engine returns structured results or a proposed tool call. It never invokes the tool,
  creates an artifact, changes workflow state, or browses neighboring files.

Until those backend contracts are agreed, do not wire this factory into `app.main`, add workflow
routes, or add backend imports under `app.ai`.

## Integration call order

Backend workflow code should call the existing interface in this order for the golden inspection
path. Each return value is typed; the backend remains responsible for state and persistence.

```text
health
→ choose_capability
→ analyze_visual
→ search_knowledge
→ create_grounded_draft
→ propose_action
```

Curated-corpus administration calls `ingest_knowledge` separately. The code workflow calls
`repair_code` only after Backend 2 returns sandbox test/error output. The AI engine never runs the
proposed action or repaired code.

Expected `AIError` subclasses are safe workflow facts, not HTTP decisions. Backend 1 should map
them to its own workflow failure state and user message without returning raw exception context.
In particular, `ModelNotInstalled`, `ModelCapacityError`, `InvalidStructuredOutput`,
`NoRelevantEvidence`, and unsupported/corrupt input errors need explicit handling.

Hardware benchmark and Jetson promotion instructions are in
[`JETSON_VALIDATION.md`](../../JETSON_VALIDATION.md).
