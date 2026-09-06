# Local model benchmark and Jetson validation

This runbook measures real local-model behaviour. It never installs a model, changes the active
profile, calls a remote endpoint, or treats prepared output as a live result.

The safe 8 GB profile remains the MVP baseline. A Jetson model is promoted only after the exact
device and exact Ollama model artifacts pass the unchanged golden evaluation three times.

## What the report contains

The benchmark JSON records only non-confidential facts:

- operating system, architecture, memory, available storage, and accelerator details;
- exact Jetson model and available JetPack/L4T, CUDA, and Ollama versions;
- configured, health-selected, and actually observed model names;
- model digest, parameter size, and quantization reported by local Ollama;
- golden quality gates, fallback/schema counts, stage timings, and tokens per second;
- sampled peak dedicated GPU memory on a workstation, or shared system-memory pressure on Jetson;
- sampled maximum temperature where the local platform exposes it.

It deliberately excludes prompts, document text, images, model responses, usernames, hostnames,
and absolute corpus paths.

## Before running

An operator must install Ollama separately and preload the approved models while following the
organization's offline provisioning process. WorkBench has no model-pull operation.

Required safe models:

```text
qwen3:4b
qwen3-vl:4b
qwen3-embedding:0.6b
```

Jetson candidate models:

```text
qwen3:8b
qwen3-vl:8b
```

Do not start the candidate run if the exact device model, memory capacity, or installed model
digests cannot be recorded.

## Record the safe workstation baseline

Create a report directory outside the repository, then run from the repository root:

```bash
WORKBENCH_AI_MODEL_PROFILE=safe-8gb \
pnpm --filter @workbench/ai evaluate:benchmark \
  --output /approved/benchmark-reports/workstation-safe.json
```

The command runs the complete golden workflow three times. A successful command means the quality
gates passed and local resource sampling worked. A JSON report is still written when quality gates
fail so the failure can be diagnosed.

## Record the Jetson safe baseline

Run the same code, corpus, prompts, schemas, and model adapter on the Jetson:

```bash
WORKBENCH_AI_MODEL_PROFILE=safe-8gb \
pnpm --filter @workbench/ai evaluate:benchmark \
  --output /approved/benchmark-reports/jetson-safe.json
```

This proves the safe fallback works on the target device before testing larger models.

## Test stronger models independently

Run the text candidate while vision stays on the safe model:

```bash
WORKBENCH_AI_MODEL_PROFILE=jetson-text-candidate \
pnpm --filter @workbench/ai evaluate:benchmark \
  --output /approved/benchmark-reports/jetson-text-8b.json
```

Then run the vision candidate while text stays on the safe model:

```bash
WORKBENCH_AI_MODEL_PROFILE=jetson-vision-candidate \
pnpm --filter @workbench/ai evaluate:benchmark \
  --output /approved/benchmark-reports/jetson-vision-8b.json
```

The local Ollama adapter serializes capability changes and unloads the active large generative
model before switching. Do not run the text and vision benchmarks concurrently.

The combined `jetson-candidate` profile exists for a final smoke test. Use the isolated profiles
for promotion decisions because they make failure ownership clear.

## Compare a candidate with the safe baseline

```bash
pnpm --filter @workbench/ai evaluate:promotion \
  --baseline /approved/benchmark-reports/jetson-safe.json \
  --candidate /approved/benchmark-reports/jetson-text-8b.json \
  --output /approved/benchmark-reports/text-promotion.json
```

Repeat with the vision report. Default limits are:

- zero structured-output failures;
- at most 90 percent peak memory pressure;
- no more than twice the safe model's capability-stage latency;
- at most 85 degrees Celsius;
- no observed thermal throttling.

Limits can be changed through explicit command flags, but the reviewed values used for the final
decision must be kept with the benchmark reports.

The comparison returns one of four outcomes for each capability:

- `promote`: the stronger model has complete evidence and passed;
- `retainSafe`: no stronger candidate applies, including the fixed embedding model;
- `reject`: the candidate was tested and failed a quality, fallback, reliability, resource, or
  latency requirement;
- `inconclusive`: required hardware, model identity, memory, or latency proof is missing.

The command writes a recommendation only. It does not edit model profiles or activate a model.

## Promotion rules

Promote text and vision independently. Keep `qwen3-embedding:0.6b` fixed; changing it requires a
new Chroma collection and complete re-indexing.

An 8B candidate is not eligible when any of these is true:

- the candidate report is not from an identified Jetson;
- exact model digest, parameter size, or quantization is missing;
- the preferred model was not used in all three runs;
- a fallback occurred;
- a capability-specific golden gate failed;
- outcomes were not reproducible;
- structured output failed beyond the approved limit;
- peak memory or latency exceeded the reviewed limit;
- measured temperature exceeded the limit or throttling was observed.

Only after human review should a separately named validated profile be added to
`app/ai/models/profiles.py`. Never change `safe-8gb`; it remains the tested fallback.

## Sovereignty checks

Before the final demonstration, run the offline tests and inspect the machine's network monitor:

```bash
pnpm --filter @workbench/ai test
```

The tests prove that non-loopback Ollama origins and model-pull endpoints are rejected, Chroma uses
local persistence with telemetry disabled, structured-output schema references cannot resolve over
the network, and benchmark reports contain no prompt/document payloads.

The OS network monitor is still required during the full application demo. Unit tests prove
policy; the monitor proves the running process made no external connection.

## Backend handoff and final smoke test

Jetson model-quality benchmarking does not require FastAPI workflow routes. Full application
validation does. After Backend 1 and Backend 2 finish integration:

1. compose one `LocalAIEngine` for the application lifespan;
2. supply only Backend 2-approved upload paths and Chroma roots;
3. run upload → vision → retrieval → draft through Backend 1's workflow controller;
4. confirm model/fallback facts appear in progress events without document content;
5. approve export and verify Backend 2 creates the artifact;
6. repeat the complete golden demo twice with the selected profile;
7. repeat once with the safe fallback profile.

AI code must not implement workflow state, approvals, artifact writing, sandbox execution, or
Electron delivery to make this smoke test pass.
