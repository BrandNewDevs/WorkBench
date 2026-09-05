"""Validated structured text generation over the injected local model seam."""

from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, JsonValue, ValidationError

from app.ai.errors import GroundingViolation, InvalidStructuredOutput, InvalidToolProposal
from app.ai.models.ports import ModelAdapter
from app.ai.models.structured_output import (
    validate_output_schema,
    validate_structured_output,
)
from app.ai.prompts.code_repair import (
    CODE_REPAIR_SYSTEM_PROMPT,
    build_code_repair_prompt,
)
from app.ai.prompts.grounded_drafting import (
    GROUNDED_DRAFTING_SYSTEM_PROMPT,
    build_grounded_drafting_prompt,
)
from app.ai.prompts.planning import (
    TASK_PLANNING_SYSTEM_PROMPT,
    build_task_planning_prompt,
)
from app.ai.prompts.tool_proposal import (
    TOOL_PROPOSAL_SYSTEM_PROMPT,
    build_tool_proposal_prompt,
)
from app.ai.schemas import (
    AgentContext,
    AgentProposal,
    CodeRepairContent,
    CodeRepairRequest,
    CodeRepairResult,
    DraftRequest,
    GroundedDraft,
    ModelProfile,
    ProposedToolCall,
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

    async def create_grounded_draft(self, request: DraftRequest) -> GroundedDraft:
        """Draft approval-note content and enforce application-owned evidence IDs."""

        allowed_source_ids = {
            evidence.source_id for evidence in request.evidence
        } | {
            source.source_id
            for finding in request.findings
            for source in finding.evidence
        }

        def validate_grounding(
            draft: GroundedDraft,
            generation: TextGenerationResult,
        ) -> None:
            del generation
            if draft.subject != request.subject:
                raise GroundingViolation("draft changed the authenticated subject")
            if draft.findings != request.findings:
                raise GroundingViolation("draft changed application-supplied findings")
            unknown_source_ids = set(draft.evidence_source_ids) - allowed_source_ids
            if unknown_source_ids:
                names = ", ".join(sorted(unknown_source_ids))
                raise GroundingViolation(
                    f"draft cited source IDs not supplied by the application: {names}"
                )

        result, _ = await self._generate_structured(
            result_type=GroundedDraft,
            system_prompt=GROUNDED_DRAFTING_SYSTEM_PROMPT,
            prompt_builder=lambda schema, retry: build_grounded_drafting_prompt(
                request,
                schema,
                retry=retry,
            ),
            temperature=0.2,
            operation_name="grounded drafting",
            result_validator=validate_grounding,
        )
        return result

    async def propose_action(self, context: AgentContext) -> AgentProposal:
        """Return one validated proposal while leaving execution to Backend 1."""

        if not context.allowed_tools:
            return AgentProposal(response_text="No backend-approved tools are available.")

        allowed_tools = {tool.name: tool for tool in context.allowed_tools}
        for tool in allowed_tools.values():
            validate_output_schema(tool.input_schema)

        def validate_proposal(
            proposal: ProposedToolCall,
            generation: TextGenerationResult,
        ) -> None:
            del generation
            tool = allowed_tools.get(proposal.tool_name)
            if tool is None:
                raise InvalidToolProposal(
                    f"tool proposal named an unapproved tool: {proposal.tool_name}"
                )
            try:
                validate_structured_output(tool.input_schema, proposal.arguments)
            except InvalidStructuredOutput as error:
                raise InvalidToolProposal(
                    f"tool proposal arguments did not match {proposal.tool_name}"
                ) from error

        result, _ = await self._generate_structured(
            result_type=ProposedToolCall,
            system_prompt=TOOL_PROPOSAL_SYSTEM_PROMPT,
            prompt_builder=lambda schema, retry: build_tool_proposal_prompt(
                context,
                schema,
                retry=retry,
            ),
            temperature=0,
            operation_name="tool proposal",
            result_validator=validate_proposal,
        )
        return AgentProposal(tool_call=result)

    async def repair_code(self, request: CodeRepairRequest) -> CodeRepairResult:
        """Suggest corrected code from feedback without running any source."""

        def validate_language(
            content: CodeRepairContent,
            generation: TextGenerationResult,
        ) -> None:
            del generation
            if content.language != request.language:
                raise InvalidStructuredOutput(
                    "code repair changed the application-supplied language"
                )

        content, generation = await self._generate_structured(
            result_type=CodeRepairContent,
            system_prompt=CODE_REPAIR_SYSTEM_PROMPT,
            prompt_builder=lambda schema, retry: build_code_repair_prompt(
                request,
                schema,
                retry=retry,
            ),
            temperature=0,
            operation_name="code repair",
            result_validator=validate_language,
        )
        return CodeRepairResult(
            language=content.language,
            corrected_code=content.corrected_code,
            change_summary=content.change_summary,
            model=generation.model,
        )

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

        message = f"{operation_name} returned invalid structured output after one retry"
        if isinstance(last_error, InvalidStructuredOutput):
            raise type(last_error)(message) from last_error
        raise InvalidStructuredOutput(message) from last_error
