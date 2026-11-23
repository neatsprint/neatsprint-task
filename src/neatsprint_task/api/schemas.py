from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    key: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,9}$")
    description: str | None = Field(default=None, max_length=4000)
    start_date: date | None = None
    end_date: date | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    start_date: date | None = None
    end_date: date | None = None


class ItemCreate(BaseModel):
    project_id: UUID
    type: Literal["TASK", "STORY", "BUG", "SUGGESTION"] = "TASK"
    title: str = Field(min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=20000)
    priority: Literal["LOW", "MEDIUM", "HIGH", "URGENT"] = "MEDIUM"
    estimate_points: float | None = Field(default=None, ge=0, le=1000)
    assignee_id: UUID | None = None
    sprint_id: UUID | None = None
    due_date: date | None = None


class ItemUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=20000)
    priority: Literal["LOW", "MEDIUM", "HIGH", "URGENT"] | None = None
    estimate_points: float | None = Field(default=None, ge=0, le=1000)
    assignee_id: UUID | None = None
    sprint_id: UUID | None = None
    due_date: date | None = None


class TransitionRequest(BaseModel):
    status: Literal["TODO", "IN_PROGRESS", "IN_REVIEW", "DONE", "BLOCKED"]
    reason: str | None = Field(default=None, max_length=500)


class AssignRequest(BaseModel):
    assignee_id: UUID | None = None


class RankRequest(BaseModel):
    before_item_id: UUID | None = None
    after_item_id: UUID | None = None


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=20000)


class SprintCreate(BaseModel):
    project_id: UUID
    name: str = Field(min_length=1, max_length=160)
    goal: str | None = Field(default=None, max_length=2000)
    start_date: date | None = None
    end_date: date | None = None


class SprintItemsRequest(BaseModel):
    item_ids: list[UUID] = Field(min_length=1)


class AttachmentPresign(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=3, max_length=128)
    size_bytes: int = Field(ge=1, le=25 * 1024 * 1024)


class AttachmentConfirm(BaseModel):
    object_key: str = Field(min_length=10, max_length=512)
    file_name: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=3, max_length=128)
    size_bytes: int = Field(ge=1, le=25 * 1024 * 1024)


class ProjectTeamsRequest(BaseModel):
    team_ids: list[UUID] = Field(default_factory=list)
