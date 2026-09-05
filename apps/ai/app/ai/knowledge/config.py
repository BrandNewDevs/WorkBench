"""Configuration for bounded local document ingestion."""

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MEBIBYTE = 1024 * 1024


class KnowledgeProcessingSettings(BaseSettings):
    """Resource limits for deterministic parsing and chunking."""

    model_config = SettingsConfigDict(
        env_prefix="WORKBENCH_KNOWLEDGE_",
        extra="ignore",
        frozen=True,
    )

    max_input_bytes: int = Field(default=25 * _MEBIBYTE, gt=0)
    max_pdf_pages: int = Field(default=250, gt=0, le=2_000)
    max_docx_entries: int = Field(default=5_000, gt=0)
    max_docx_uncompressed_bytes: int = Field(default=100 * _MEBIBYTE, gt=0)
    max_document_blocks: int = Field(default=20_000, gt=0)
    max_heading_chars: int = Field(default=240, ge=32, le=1_000)
    max_chunk_chars: int = Field(default=2_400, ge=512, le=12_000)

    @model_validator(mode="after")
    def heading_fits_inside_a_chunk(self) -> "KnowledgeProcessingSettings":
        """Reserve useful chunk space for the rule or procedure body."""

        if self.max_heading_chars > self.max_chunk_chars // 2:
            raise ValueError("maximum heading length must not consume most of a chunk")
        return self
