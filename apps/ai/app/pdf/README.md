# Local PDF conversations

The Electron PDF view uses a separate `pdfDocument` session. Local chat remains
text-only. One uploaded PDF is bound to its authenticated session; source paths
never cross into the renderer. Existing SQLite sessions are migrated in place.

## Read and answer

Upload binds source metadata immediately. The first turn extracts native page
blocks and tables, analyzes only pages needing vision, and indexes page chunks
in a separate owner/session/source-filtered Chroma collection. Q&A uses retrieved
pages; summaries use bounded hierarchical reduction. Application code validates
page references. Empty retrieval returns an explicit missing-evidence answer.

Text PDFs need the configured text **and embedding** models. Scanned or mixed
pages additionally need the configured vision model. Models must already be
installed in local Ollama; this workflow never downloads them.

## Streaming and approvals

Ordinary chat streams Ollama's answer envelope. PDF summaries and answers first
produce a validated grounded result, then use a separate real Ollama streaming
pass to present it. The final presentation must equal the validated answer.
This intentionally adds latency; it is not simulated token playback. Failed
attempts reset provisional text. Only completed validated turns are persisted.

Create/edit proposals are structured, non-streaming plans. No artifact is
written until an authenticated approval claims the exact plan hash and output
destination. A repeated approval does not execute twice. Interrupted claims
cannot be replayed. The renderer also requires the database-backed write policy.

Creation uses a bundled ReportLab layout. Editing creates a copy and supports
bounded replacement/redaction, overlays, annotations, page selection/reordering/
duplication, and added generated pages. Replacement requires an available font
and text that fits. Scanned text reconstruction, arbitrary reflow, signed-PDF
editing and exact unavailable-font reproduction are not supported. Output is
reopened and rendered before registration; unchanged regions are compared for
preserved-layout edits. Failed output is removed.

## Backend seam

- `GET /pdf/sessions/{id}` restores source, turns, approvals and activity.
- `POST /pdf/sessions/{id}/turns[/stream]` handles PDF turns.
- `POST /pdf/sessions/{id}/approvals/{approvalId}` approves/rejects an exact plan.
- `GET /pdf/sessions/{id}/artifacts/{artifactId}` is consumed by Electron main.
- `POST /pdf/sessions/{id}/delete` removes session files and PDF index records.

Electron main verifies artifact bytes and handles Open/Save as. The renderer
receives no backend path. Activity contains operation labels, not model tokens.

## Acceptance

Offline tests live in `tests/pdf`; run them from the service root with pytest.
The opt-in `live_ollama` PDF test runs three summary turns with installed approved
text/embedding models. It skips when explicitly disabled or models are absent.
Skipping is not evidence of live acceptance.

On the target machine, verify native summary, cited follow-up, refresh/restore,
approval rejection, approved creation, safe replacement, Open/Save as, stream
cancellation, and Enter versus Shift+Enter. Repeat scanned/mixed cases with the
vision model. Check actual selected model/fallback and local-only traffic.
Windows dialogs and laptop/Jetson model behaviour require target-machine tests.
