# Sanitized golden AI corpus

This corpus is entirely synthetic. It contains no organization, employee, vendor,
facility, or operational data. The pump and every inspection fact are fictional.

The fixed corpus version is `pump-inspection-v1`. Change the version and expected
results together whenever an input or acceptance rule changes. The scanned report is
an image-only PDF so unit and live runs exercise the visual pipeline instead of the
native PDF text parser.

Run `python scripts/build_golden_report.py` from `apps/ai` only when deliberately
regenerating the scanned fixture, then inspect every rendered page before committing it.
