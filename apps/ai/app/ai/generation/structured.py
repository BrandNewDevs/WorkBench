"""Validated structured text generation over the injected local model seam."""

from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, JsonValue, ValidationError

from app.ai.errors import InvalidStructuredOutput
from app.ai.models.ports import ModelAdapter
from app.ai.prompts.planning import (
    TASK_PLANNING_SYSTEM_PROMPT,
    build_task_planning_prompt,
)
from app.ai.schemas import (
    AgentContext,
    ModelProfile,
    TaskPlan,
    TextGenerationRequest,
    TextGenerationResult,
)

StructuredResultT = TypeVar("StructuredResultT", bound=BaseModel)
PromptBuilder = Callable[[dict[str, JsonValue], bool], str]
ResultValidator = Callable[[StructuredResultT, TextGenerationResult], None]


class StructuredTextGenerator:
    """Hide prompts, retries, and validation behind typed workflow methods."""

    def __init__(self, model_adapter: ModelAdapter, model_profile: ModelProfile) -> None:
        self._model_adapter = model_adapter
        self._model_profile = model_profile

    async def plan_task(self, context: AgentContext) -> TaskPlan:
        """Propose a typed sequence without executing or approving any step."""

        result, _ = await self._generate_structured(
            result_type=TaskPlan,
            system_prompt=TASK_PLANNING_SYSTEM_PROMPT,
            prompt_builder=lambda schema, retry: build_task_planning_prompt(
                context,
                schema,
                retry=retry,
            ),
            temperature=0,
            operation_name="task planning",
        )
        return result

    async def _generate_structured(
        self,
        *,
        result_type: type[StructuredResultT],
        system_prompt: str,
        prompt_builder: PromptBuilder,
        temperature: float,
        operation_name: str,
        result_validator: ResultValidator[StructuredResultT] | None = None,
    ) -> tuple[StructuredResultT, TextGenerationResult]:
        schema: dict[str, JsonValue] = result_type.model_json_schema(by_alias=True)
        last_error: InvalidStructuredOutput | ValidationError | None = None

        for attempt in range(2):
            request = TextGenerationRequest(
                model=self._model_profile.text_candidates[0],
                system_prompt=system_prompt,
                user_prompt=prompt_builder(schema, attempt == 1),
                output_schema=schema,
                limits=self._model_profile.text_limits,
                temperature=temperature,
            )
            try:
                generation = await self._model_adapter.generate_text(request)
                result = result_type.model_validate(generation.structured_output)
                if result_validator is not None:
                    result_validator(result, generation)
            except (InvalidStructuredOutput, ValidationError) as error:
                last_error = error
                continue
            return result, generation

        raise InvalidStructuredOutput(
            f"{operation_name} returned invalid structured output after one retry"
        ) from last_error
