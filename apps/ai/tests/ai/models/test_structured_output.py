"""Reference resolution must remain offline, including without schema preflight."""

from unittest.mock import patch

import pytest
from pydantic import JsonValue

from app.ai.errors import InvalidStructuredOutput, OllamaPolicyViolation
from app.ai.models.structured_output import (
    validate_output_schema,
    validate_structured_output,
)


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef", "$recursiveRef"])
def test_preflight_rejects_external_reference_keywords(keyword: str) -> None:
    schema: dict[str, JsonValue] = {
        "properties": {"result": {keyword: "https://example.invalid/schema"}}
    }
    with pytest.raises(OllamaPolicyViolation, match="external"):
        validate_output_schema(schema)


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef"])
def test_output_validation_never_fetches_a_remote_schema(keyword: str) -> None:
    schema: dict[str, JsonValue] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        keyword: "https://example.invalid/schema",
    }
    with patch("urllib.request.urlopen", side_effect=AssertionError("network attempted")) as fetch:
        with pytest.raises(InvalidStructuredOutput, match="invalid reference"):
            validate_structured_output(schema, {})
        fetch.assert_not_called()


def test_local_definitions_still_validate_model_output() -> None:
    schema: dict[str, JsonValue] = {
        "$defs": {"finding": {"type": "string", "minLength": 1}},
        "type": "object",
        "properties": {"finding": {"$ref": "#/$defs/finding"}},
        "required": ["finding"],
    }
    validate_output_schema(schema)
    validate_structured_output(schema, {"finding": "Corrosion visible"})
    with pytest.raises(InvalidStructuredOutput):
        validate_structured_output(schema, {"finding": 42})
