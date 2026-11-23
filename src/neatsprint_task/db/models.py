"""Task service models: projects, backlog, sprints and comments."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from neatsprint_lib.db import Base, SoftDeleteMixin, TenantMixin, TimestampMixin
from neatsprint_lib.events import OutboxEvent as OutboxEvent  # noqa: F401 - register table
from neatsprint_lib.ids import new_id
from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

ITEM_TYPES = ("TASK", "STORY", "BUG", "SUGGESTION")
ITEM_STATUSES = ("TODO", "IN_PROGRESS", "IN_REVIEW", "DONE", "BLOCKED")
PRIORITIES = ("LOW", "MEDIUM", "HIGH", "URGENT")
SPRINT_STATES = ("PLANNED", "ACTIVE", "COMPLETED")


class Project(Base, TenantMixin, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="uq_projects_tenant_key"),
        CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_id
    )
    key: Mapped[str] = mapped_column(String(12), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    next_item_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ProjectTeam(Base, TenantMixin):
    __tablename__ = "project_teams"

    project_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    team_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)


class Sprint(Base, TenantMixin, TimestampMixin):
    __tablename__ = "sprints"
    __table_args__ = (
        CheckConstraint("state IN ('PLANNED','ACTIVE','COMPLETED')", name="state"),
        Index("ix_sprints_project_state", "project_id", "state"),
        Index(
            "ux_one_active_sprint",
            "project_id",
            unique=True,
            postgresql_where=text("state = 'ACTIVE'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_id
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    goal: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="PLANNED")
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    committed_points: Mapped[float | None] = mapped_column(Numeric(8, 2))


class BacklogItem(Base, TenantMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "backlog_items"
    __table_args__ = (
        UniqueConstraint("project_id", "sequence", name="uq_items_project_sequence"),
        UniqueConstraint("project_id", "key", name="uq_items_project_key"),
        CheckConstraint(
            "type IN ('TASK','STORY','BUG','SUGGESTION')", name="type"
        ),
        CheckConstraint(
            "status IN ('TODO','IN_PROGRESS','IN_REVIEW','DONE','BLOCKED')", name="status"
        ),
        CheckConstraint("priority IN ('LOW','MEDIUM','HIGH','URGENT')", name="priority"),
        Index("ix_items_board", "tenant_id", "project_id", "sprint_id", "status", "rank"),
        Index("ix_items_assignee", "tenant_id", "assignee_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_id
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="TASK")
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="TODO")
    priority: Mapped[str] = mapped_column(String(8), nullable=False, default="MEDIUM")
    estimate_points: Mapped[float | None] = mapped_column(Numeric(6, 2))
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    sprint_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=1024)
    due_date: Mapped[date | None] = mapped_column(Date)
    reporter_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SprintItem(Base, TenantMixin):
    __tablename__ = "sprint_items"

    sprint_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sprints.id", ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("backlog_items.id", ondelete="CASCADE"), primary_key=True
    )
    committed_points: Mapped[float | None] = mapped_column(Numeric(6, 2))
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ItemComment(Base, TenantMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "item_comments"
    __table_args__ = (Index("ix_comments_item", "item_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_id
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("backlog_items.id", ondelete="CASCADE"), nullable=False
    )
    author_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ItemHistory(Base, TenantMixin):
    __tablename__ = "item_history"
    __table_args__ = (Index("ix_history_item", "item_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_id
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("backlog_items.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    field: Mapped[str] = mapped_column(String(32), nullable=False)
    from_value: Mapped[str | None] = mapped_column(String(240))
    to_value: Mapped[str | None] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
