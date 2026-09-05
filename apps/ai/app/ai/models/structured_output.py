"""Offline JSON Schema validation for application-owned structured outputs."""

from jsonschema import SchemaError
from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema.validators import validator_for
from pydantic import JsonValue
from referencing import Registry
from referencing.exceptions import Unresolvable

from app.ai.errors import InvalidStructuredOutput, OllamaPolicyViolation


def validate_output_schema(schema: dict[str, JsonValue]) -> None:
    """Validate a JSON Schema and reject references that could leave the machine."""

    _reject_external_references(schema)
    validator_class = validator_for(schema)
    try:
        validator_class.check_schema(schema)
    except SchemaError as error:
        raise InvalidStructuredOutput("configured output schema is invalid") from error


def validate_structured_output(
    schema: dict[str, JsonValue],
    structured_output: JsonValue,
) -> None:
    """Require a model response to satisfy the exact schema sent to Ollama."""

    validator_class = validator_for(schema)
    try:
        # An explicit empty registry has no network retriever, even if a caller
        # bypasses the schema preflight or a new reference keyword is introduced.
        validator_class(schema, registry=Registry()).validate(structured_output)
    except JsonSchemaValidationError as error:
        raise InvalidStructuredOutput(
            "Ollama output did not match the configured schema"
        ) from error
    except Unresolvable as error:
        raise InvalidStructuredOutput(
            "configured output schema has an invalid reference"
        ) from error


def _reject_external_references(value: JsonValue) -> None:
    if isinstance(value, dict):
        for keyword in ("$ref", "$dynamicRef", "$recursiveRef"):
            reference = value.get(keyword)
            if isinstance(reference, str) and not reference.startswith("#"):
                raise OllamaPolicyViolation("external JSON Schema references are not allowed")
        for nested_value in value.values():
            _reject_external_references(nested_value)
    elif isinstance(value, list):
        for nested_value in value:
            _reject_external_references(nested_value)
