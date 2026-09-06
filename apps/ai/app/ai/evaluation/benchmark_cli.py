"""Operator command for a local hardware-bound golden benchmark report."""

import argparse
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from app.ai.errors import AIError
from app.ai.evaluation.benchmark import build_benchmark_report
from app.ai.evaluation.corpus import load_golden_corpus
from app.ai.evaluation.evaluator import GoldenEvaluator
from app.ai.evaluation.hardware import LocalHardwareProbe, LocalResourceMonitor
from app.ai.models import create_ollama_adapter, load_model_profile
from app.ai.schemas import ApprovedKnowledgeRoot

_SERVICE_ROOT = Path(__file__).parents[3]
_DEFAULT_CORPUS = _SERVICE_ROOT / "tests" / "fixtures" / "golden"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the unchanged local golden suite and record sanitized hardware/model metrics."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Benchmark JSON path; its parent directory must already exist.",
    )
    parser.add_argument(
        "--corpus-root",
        type=Path,
        default=_DEFAULT_CORPUS,
        help="Versioned sanitized corpus root.",
    )
    parser.add_argument(
        "--storage-root",
        type=Path,
        default=_SERVICE_ROOT,
        help="Existing local storage volume whose available capacity should be recorded.",
    )
    parser.add_argument(
        "--sample-interval-seconds",
        type=float,
        default=1.0,
        help="Positive resource-sampling interval used while the golden workflow runs.",
    )
    return parser.parse_args()


async def _run(
    output: Path,
    corpus_root: Path,
    storage_root: Path,
    sample_interval_seconds: float,
) -> bool:
    destination = _validated_destination(output)
    profile = load_model_profile()
    hardware = LocalHardwareProbe().capture(storage_root)
    monitor = LocalResourceMonitor(
        hardware,
        sample_interval_seconds=sample_interval_seconds,
    )
    adapter = create_ollama_adapter(profile=profile)
    try:
        runtime_health = await adapter.health(profile)
        installed_models = await adapter.list_models() if runtime_health.runtime_ready else ()
        with TemporaryDirectory(prefix="workbench-benchmark-") as storage:
            evaluator = GoldenEvaluator(
                corpus=load_golden_corpus(corpus_root),
                model_adapter=adapter,
                model_profile=profile,
                knowledge_root=ApprovedKnowledgeRoot(path=Path(storage)),
            )
            golden_suite, resources = await monitor.measure(evaluator.run_three_times)
    finally:
        await adapter.close()

    report = build_benchmark_report(
        profile=profile,
        hardware=hardware,
        runtime_health=runtime_health,
        installed_models=installed_models,
        golden_suite=golden_suite,
        resources=resources,
    )
    destination.write_text(
        report.model_dump_json(by_alias=True, indent=2) + "\n",
        encoding="utf-8",
    )
    complete = report.golden_suite.passed and report.resources.sample_count > 0
    print(
        f"Local benchmark {'passed' if complete else 'needs attention'} for profile "
        f"'{report.profile_id}'; report written to {destination}"
    )
    if not report.golden_suite.passed:
        print("- The golden quality or reproducibility gates did not all pass.")
    if report.resources.sample_count == 0:
        print("- Peak memory was not measured; this report cannot approve model promotion.")
    for diagnostic in report.hardware.diagnostics:
        print(f"- {diagnostic}")
    for diagnostic in report.resources.diagnostics:
        print(f"- {diagnostic}")
    return complete


def _validated_destination(output: Path) -> Path:
    output_parent = output.parent.resolve(strict=True)
    if not output_parent.is_dir():
        raise ValueError("benchmark report parent must be an existing directory")
    return output_parent / output.name


def main() -> int:
    """Return a shell-friendly result without dumping confidential exception context."""

    arguments = _arguments()
    try:
        passed = asyncio.run(
            _run(
                arguments.output,
                arguments.corpus_root,
                arguments.storage_root,
                arguments.sample_interval_seconds,
            )
        )
    except (AIError, OSError, UnicodeError, ValueError) as error:
        print(f"Local benchmark could not start: {type(error).__name__}: {error}")
        return 2
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
