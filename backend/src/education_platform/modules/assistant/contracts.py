"""Pydantic contracts for LangGraph state, LLM JSON, and tool I/O."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from education_platform.modules.rag.contracts import DocKind


class ChatHistoryTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class CitationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)


class RetrievedChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    page_number: int | None = None
    doc_kind: str = Field(min_length=1)
    doc_id: str = Field(min_length=1)
    distance: float = Field(ge=0.0)


class RetrieveChunksArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4000)
    limit: int | None = Field(default=None, ge=1, le=20)
    doc_kind: DocKind | None = None

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("query must not be blank")
        return cleaned


class RetrieveChunksResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunks: list[RetrievedChunk]
    count: int = Field(ge=0)

    @model_validator(mode="after")
    def count_matches_chunks(self) -> Self:
        if self.count != len(self.chunks):
            raise ValueError("count must equal len(chunks)")
        return self


class InjectionClassifierResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    injection: bool
    reason: str = ""


class QuestionValidatorResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    valid: bool
    reason: str = ""


class AssistantGraphState(BaseModel):
    """Validated graph state passed between LangGraph nodes."""

    model_config = ConfigDict(extra="forbid")

    user_message: str = Field(min_length=1, max_length=8000)
    history: list[ChatHistoryTurn] = Field(default_factory=list)
    user_id: str = Field(min_length=1)
    institution_id: str = Field(min_length=1)
    user_email: str = Field(min_length=1)
    roles: list[str] = Field(min_length=1)
    user_status: str = Field(default="active", min_length=1)
    injection_blocked: bool = False
    question_valid: bool = True
    injection_probability: float | None = None
    guard_stage: str | None = None
    early_reply: str | None = None
    retrieved_chunks: list[RetrievedChunk] = Field(default_factory=list)
    assistant_content: str = ""
    citations: list[CitationModel] = Field(default_factory=list)
    prompt_tokens: int = Field(default=0, ge=0)

    @field_validator("user_message")
    @classmethod
    def strip_user_message(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("user_message must not be blank")
        return cleaned


def parse_graph_state(data: dict[str, Any]) -> AssistantGraphState:
    return AssistantGraphState.model_validate(data)


def dump_graph_state(state: AssistantGraphState) -> dict[str, Any]:
    return state.model_dump(mode="python")
