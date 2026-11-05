"""The shape of the model's answer: one Finding per control, with quoted citations.

These models are sent to Gemini as the JSON response schema and are also used to
validate what comes back. Plain Pydantic only (no FastAPI or database imports).
"""

from pydantic import BaseModel, ConfigDict, Field

from app.ai.controls import Label  # the one definition of the labels (D-09)


class Citation(BaseModel):
    """A passage the model says supports its finding. Not trusted until
    app/ai/citations.py has found it in the document."""

    model_config = ConfigDict(extra="forbid")

    page: int = Field(description="1-based page number, from the [PAGE n] marker.")
    quote: str = Field(description="A short passage copied verbatim from that page.")


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    control_code: str = Field(description="One of the control codes listed in the request.")
    # The SDK uses Label's docstring as this field's description (a Field description
    # here is ignored when it inlines the enum), so that docstring is model-facing.
    label: Label
    reason: str = Field(description="One or two sentences explaining the label.")
    missing_info: str | None = Field(
        default=None,
        description="What the reviewer would still need to see, or null if nothing is missing.",
    )
    citations: list[Citation]


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[Finding]
