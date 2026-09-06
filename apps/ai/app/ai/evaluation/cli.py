"""Operator command for recording a three-run live golden evaluation."""

import argparse
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from app.ai.errors import AIError
from app.ai.evaluation.corpus import load_golden_corpus
from app.ai.evaluation.evaluator import GoldenEvaluator
from app.ai.models import create_ollama_adapter, load_model_profile
from app.ai.schemas import ApprovedKnowledgeRoot

_SERVICE_ROOT = Path(__file__).parents[3]
_DEFAULT_CORPUS = _SERVICE_ROOT / "tests" / "fixtures" / "golden"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the local golden AI workflow three times and record its result."
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="JSON report path; its parent directory must already exist.",
    )
    parser.add_argument(
        "--corpus-root",
        type=Path,
        default=_DEFAULT_CORPUS,
        help="Versioned sanitized corpus root.",
    )
    return parser.parse_args()


async def _run(output: Path, corpus_root: Path) -> bool:
    output_parent = output.parent.resolve(strict=True)
    if not output_parent.is_dir():
        raise ValueError("golden report parent must be an existing directory")
    destination = output_parent / output.name
    profile = load_model_profile()
    adapter = create_ollama_adapter(profile=profile)
    try:
        with TemporaryDirectory(prefix="workbench-golden-") as storage:
            evaluator = GoldenEvaluator(
                corpus=load_golden_corpus(corpus_root),
                model_adapter=adapter,
                model_profile=profile,
                knowledge_root=ApprovedKnowledgeRoot(path=Path(storage)),
            )
            result = await evaluator.run_three_times()
    finally:
        await adapter.close()

    destination.write_text(
        result.model_dump_json(by_alias=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Golden evaluation {'passed' if result.passed else 'failed'}; "
        f"report written to {destination}"
    )
    for diagnostic in result.diagnostics:
        print(f"- {diagnostic}")
    for run in result.runs:
        for diagnostic in run.diagnostics:
            print(f"- run {run.run_number}: {diagnostic}")
    return result.passed


def main() -> int:
    """Return a shell-friendly status without exposing an unstructured traceback."""

    arguments = _arguments()
    try:
        passed = asyncio.run(_run(arguments.output, arguments.corpus_root))
    except (AIError, OSError, UnicodeError, ValueError) as error:
        print(f"Golden evaluation could not start: {type(error).__name__}: {error}")
        return 2
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
