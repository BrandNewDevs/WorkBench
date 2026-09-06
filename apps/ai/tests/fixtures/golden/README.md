# Sanitized golden AI corpus

This corpus is entirely synthetic. It contains no organization, employee, vendor,
facility, or operational data. The pump and every inspection fact are fictional.

The fixed corpus version is `pump-inspection-v1`. Change the version and expected
results together whenever an input or acceptance rule changes. The scanned report is
an image-only PDF so unit and live runs exercise the visual pipeline instead of the
native PDF text parser.

Run `python scripts/build_golden_report.py` from `apps/ai` only when deliberately
regenerating the scanned fixture, then inspect every rendered page before committing it.

## Deterministic and live use

Normal `pnpm test` runs the complete workflow three times with recorded model responses and a
fresh temporary Chroma store. It never starts Ollama.

On the final demo machine, preload every model in the selected profile and run:

```sh
pnpm --filter @workbench/ai test:live
pnpm --filter @workbench/ai evaluate:golden --output ./golden-result.json
```

The second command writes a local machine-readable report. It never pulls a model. A skipped live
test means that Ollama or a required model was unavailable and does **not** satisfy final-machine
acceptance.
