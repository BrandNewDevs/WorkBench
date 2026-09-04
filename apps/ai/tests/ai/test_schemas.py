"""Validation tests for the Phase 0 AI data contracts."""

import pytest
from pydantic import ValidationError

from app.ai.evaluation.samples import representative_contracts, sample_model_profile
from app.ai.schemas import AgentProposal, Capability, CapabilityDecision, ModelHealth, ModelStatus


def test_every_contract_accepts_a_representative_example() -> None:
    """Keep every public schema constructible without a running service."""

    contracts = representative_contracts()

    assert contracts
    assert all(contract.model_dump() for contract in contracts)


def test_contracts_serialize_with_camel_case_json_fields() -> None:
    """Honor the explicit JSON naming convention used by Backend 1."""

    payload = sample_model_profile().model_dump(by_alias=True, mode="json")

    assert payload["profileId"] == "safe-8gb"
    assert payload["textCandidates"] == ["qwen3:4b", "qwen3:1.7b"]
    assert "profile_id" not in payload


@pytest.mark.parametrize(
    ("response_text", "tool_call"),
    [(None, None), ("Done", {"toolName": "save", "arguments": {}, "explanation": "Save"})],
)
def test_agent_proposal_requires_exactly_one_outcome(
    response_text: str | None, tool_call: object | None
) -> None:
    """Reject missing outcomes and response/tool combinations."""

    with pytest.raises(ValidationError):
        AgentProposal.model_validate({"responseText": response_text, "toolCall": tool_call})


def test_ready_model_health_requires_a_loadable_selected_model() -> None:
    """Do not describe a missing model as ready."""

    with pytest.raises(ValidationError):
        ModelHealth(
            capability=Capability.TEXT,
            status=ModelStatus.READY,
            installed=False,
            loadable=False,
        )


def test_fallback_decision_requires_a_reason() -> None:
    """Fallback selection must remain visible for status and audit events."""

    with pytest.raises(ValidationError):
        CapabilityDecision(
            capability=Capability.TEXT,
            selected_model="qwen3:1.7b",
            reason="Preferred model was not selected.",
            used_fallback=True,
        )
