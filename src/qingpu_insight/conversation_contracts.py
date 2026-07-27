from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProviderName = Literal["ollama", "gemini", "rule"]
ConversationStatus = Literal["empty", "importing", "ready", "needs_attention"]
MessageRole = Literal["user", "assistant", "system"]


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=120)


class ListingImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2048)


class ReplyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=4000)
    evidence_revision: int = Field(ge=1)


class ConversationView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    status: ConversationStatus
    default_provider: ProviderName
    default_model: str
    active_evidence_revision: int | None
    created_at: datetime
    updated_at: datetime
