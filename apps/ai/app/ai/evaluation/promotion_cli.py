"""Compare two local benchmark files without activating a model profile."""

import argparse
from pathlib import Path

from pydantic import ValidationError

from app.ai.evaluation.benchmark import compare_for_promotion
from app.ai.evaluation.benchmark_contracts import (
    BenchmarkReport,
    PromotionOutcome,
    PromotionThresholds,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare safe and Jetson benchmark reports without changing configuration."
    )
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Promotion JSON path; its parent directory must already exist.",
    )
    parser.add_argument("--max-duration-ratio", type=float, default=2.0)
    parser.add_argument("--max-memory-pressure", type=float, default=0.9)
    parser.add_argument("--max-temperature-celsius", type=float, default=85.0)
    parser.add_argument("--maximum-schema-failures", type=int, default=0)
    return parser.parse_args()


def _load_report(path: Path) -> BenchmarkReport:
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("benchmark input must be a regular file")
    return BenchmarkReport.model_validate_json(resolved.read_text(encoding="utf-8"))


def _run(arguments: argparse.Namespace) -> bool:
    destination_parent = arguments.output.parent.resolve(strict=True)
    if not destination_parent.is_dir():
        raise ValueError("promotion report parent must be an existing directory")
    thresholds = PromotionThresholds(
        max_operation_duration_ratio=arguments.max_duration_ratio,
        max_memory_pressure_ratio=arguments.max_memory_pressure,
        max_temperature_celsius=arguments.max_temperature_celsius,
        maximum_schema_failures=arguments.maximum_schema_failures,
    )
    result = compare_for_promotion(
        _load_report(arguments.baseline),
        _load_report(arguments.candidate),
        thresholds=thresholds,
    )
    destination = destination_parent / arguments.output.name
    destination.write_text(
        result.model_dump_json(by_alias=True, indent=2) + "\n",
        encoding="utf-8",
    )
    for decision in result.decisions:
        print(
            f"- {decision.capability.value}: {decision.outcome.value}; "
            f"recommended model '{decision.recommended_model}'"
        )
        for reason in decision.reasons:
            print(f"  {reason}")
    print(f"Promotion report written to {destination}; no configuration was changed.")
    return not any(
        decision.outcome is PromotionOutcome.INCONCLUSIVE
        for decision in result.decisions
        if decision.capability.value != "embedding"
    )


def main() -> int:
    """Return a shell-friendly comparison result."""

    arguments = _arguments()
    try:
        complete = _run(arguments)
    except (OSError, UnicodeError, ValueError, ValidationError) as error:
        print(f"Promotion comparison failed: {type(error).__name__}: {error}")
        return 2
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
