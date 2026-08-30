"""Dependency-light public response models shared by backend and frontend."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class HealthResponse(BaseModel):
    """Backend liveness response."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    status: Literal["ok"] = Field(
        default="ok",
        description="Fixed value confirming that the backend process is alive.",
    )


class SourceReference(BaseModel):
    """Public citation metadata derived from one local tool result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(description="Human-readable source title.")
    url: HttpUrl = Field(description="Official public source URL.")
    source_type: str = Field(
        description="Evidence source category, such as xsd or umsetzungsleitfaden."
    )
    section: str | None = Field(
        default=None,
        description="Source section heading when the evidence comes from prose.",
    )
    obds_version: str | None = Field(
        default=None,
        description="Exact oBDS version, or null for version-independent evidence.",
    )
    source_id: int | None = Field(
        default=None,
        gt=0,
        description="Request-local stored document identifier for prose evidence.",
    )
    xsd_file: str | None = Field(
        default=None,
        description="Official XSD filename for schema evidence.",
    )
    element: str | None = Field(
        default=None,
        description="XML element name for schema evidence.",
    )
    path: str | None = Field(
        default=None,
        description="Canonical XML path for schema evidence.",
    )


class QueryResponse(BaseModel):
    """LLM answer with used schema versions and deduplicated evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: str = Field(
        description="Complete model answer grounded in the returned sources."
    )
    used_versions: tuple[str, ...] = Field(
        description="oBDS versions used by successful retrieval during the request."
    )
    sources: tuple[SourceReference, ...] = Field(
        description="Deduplicated evidence cited by the answer, in citation order."
    )


class ErrorResponse(BaseModel):
    """User-safe error returned for an expected backend failure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    detail: str = Field(description="Stable description of the request failure.")


class SchemaEnumValue(BaseModel):
    """One allowed XSD enumeration value and its optional documentation."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    value: str = Field(description="Allowed lexical value from the XSD enumeration.")
    documentation: str | None = Field(
        default=None,
        description="Documentation attached to this enumeration value.",
    )


class SchemaSourceLine(BaseModel):
    """One numbered XSD source line and its evidence-highlight state."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    number: int = Field(gt=0, description="One-based source line number.")
    content: str = Field(description="Original XSD source line content.")
    highlighted: bool = Field(
        description="Whether this line belongs to the selected declaration."
    )


class XsdEvidenceResponse(BaseModel):
    """Public facts and exact source lines for one XSD element occurrence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(description="XML element name.")
    path: str = Field(description="Canonical path of this element occurrence.")
    datatype: str = Field(description="Declared or resolved XSD datatype.")
    base_datatype: str | None = Field(
        default=None,
        description="Primitive XSD base datatype when available.",
    )
    min_occurs: int = Field(
        ge=0,
        description="Minimum number of allowed occurrences.",
    )
    max_occurs: int | Literal["unbounded"] = Field(
        description="Maximum occurrences, or unbounded when no finite limit exists."
    )
    allowed_values: tuple[SchemaEnumValue, ...] = Field(
        description="Enumeration values allowed by the element datatype."
    )
    documentation: str | None = Field(
        default=None,
        description="Documentation attached directly to the element declaration.",
    )
    datatype_documentation: str | None = Field(
        default=None,
        description="Documentation inherited from the element datatype.",
    )
    version: str = Field(description="Exact oBDS schema version.")
    xsd_file: str = Field(description="Official XSD filename.")
    source_url: HttpUrl = Field(description="Official public XSD URL.")
    source_lines: tuple[SchemaSourceLine, ...] = Field(
        description="Bounded numbered excerpt around the element declaration."
    )
    declaration_start_line: int | None = Field(
        default=None,
        gt=0,
        description="First line of the complete declaration when located.",
    )
    declaration_end_line: int | None = Field(
        default=None,
        gt=0,
        description="Last line of the complete declaration when located.",
    )
    declaration_truncated: bool = Field(
        description="Whether the bounded excerpt omits part of the declaration."
    )
