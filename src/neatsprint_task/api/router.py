from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, Response
from neatsprint_lib.auth import Principal, require_permission
from neatsprint_lib.db import get_session
from neatsprint_lib.errors import ApiError
from neatsprint_lib.ids import new_id
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from neatsprint_task.api.schemas import (
    AssignRequest,
    CommentCreate,
    ItemCreate,
    ItemUpdate,
    ProjectCreate,
    ProjectUpdate,
    RankRequest,
    SprintCreate,
    SprintItemsRequest,
    TransitionRequest,
)
from neatsprint_task.db.models import (
    ITEM_STATUSES,
    BacklogItem,
    ItemComment,
    ItemHistory,
    Project,
    Sprint,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])
internal_router = APIRouter(prefix="/internal", tags=["internal"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ProjectView = Annotated[Principal, Depends(require_permission("task:project:view"))]
ProjectCreatePerm = Annotated[Principal, Depends(require_permission("task:project:create"))]
ProjectEdit = Annotated[Principal, Depends(require_permission("task:project:edit"))]
ProjectArchive = Annotated[Principal, Depends(require_permission("task:project:archive"))]
ItemView = Annotated[Principal, Depends(require_permission("task:item:view"))]
ItemCreatePerm = Annotated[Principal, Depends(require_permission("task:item:create"))]
ItemEdit = Annotated[Principal, Depends(require_permission("task:item:edit"))]
ItemTransition = Annotated[Principal, Depends(require_permission("task:item:transition"))]
ItemAssign = Annotated[Principal, Depends(require_permission("task:item:assign"))]
ItemCommentPerm = Annotated[Principal, Depends(require_permission("task:item:comment"))]
SprintView = Annotated[Principal, Depends(require_permission("task:sprint:view"))]
SprintManage = Annotated[Principal, Depends(require_permission("task:sprint:manage"))]

TRANSITIONS: dict[str, set[str]] = {
    "TODO": {"IN_PROGRESS", "BLOCKED"},
    "IN_PROGRESS": {"TODO", "IN_REVIEW", "BLOCKED", "DONE"},
    "IN_REVIEW": {"IN_PROGRESS", "DONE", "BLOCKED"},
    "BLOCKED": {"TODO", "IN_PROGRESS"},
    "DONE": {"IN_PROGRESS"},
}


def envelope(request: Request, data: object, **meta: object) -> dict[str, object]:
    return {
        "data": data,
        "meta": {"request_id": getattr(request.state, "request_id", None), **meta},
    }


def project_payload(project: Project) -> dict[str, object]:
    return {
        "id": str(project.id),
        "key": project.key,
        "name": project.name,
        "description": project.description,
        "status": project.status,
        "start_date": project.start_date.isoformat() if project.start_date else None,
        "end_date": project.end_date.isoformat() if project.end_date else None,
    }


def item_payload(item: BacklogItem) -> dict[str, object]:
    return {
        "id": str(item.id),
        "key": item.key,
        "project_id": str(item.project_id),
        "type": item.type,
        "title": item.title,
        "description": item.description,
        "status": item.status,
        "priority": item.priority,
        "estimate_points": (
            float(item.estimate_points) if item.estimate_points is not None else None
        ),
        "assignee_id": str(item.assignee_id) if item.assignee_id else None,
        "sprint_id": str(item.sprint_id) if item.sprint_id else None,
        "rank": item.rank,
        "due_date": item.due_date.isoformat() if item.due_date else None,
        "reporter_id": str(item.reporter_id) if item.reporter_id else None,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
    }


def sprint_payload(sprint: Sprint) -> dict[str, object]:
    return {
        "id": str(sprint.id),
        "project_id": str(sprint.project_id),
        "name": sprint.name,
        "goal": sprint.goal,
        "state": sprint.state,
        "start_date": sprint.start_date.isoformat() if sprint.start_date else None,
        "end_date": sprint.end_date.isoformat() if sprint.end_date else None,
        "committed_points": float(sprint.committed_points)
        if sprint.committed_points is not None
        else None,
    }


def history(
    session: AsyncSession,
    *,
    item: BacklogItem,
    actor_id: UUID,
    field: str,
    before: str | None,
    after: str | None,
) -> None:
    session.add(
        ItemHistory(
            tenant_id=item.tenant_id,
            item_id=item.id,
            actor_id=actor_id,
            field=field,
            from_value=before,
            to_value=after,
        )
    )


async def _project(session: AsyncSession, tenant_id: UUID, project_id: UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.tenant_id != tenant_id:
        raise ApiError(404, "not_found", "Project not found.")
    return project


async def _item(session: AsyncSession, tenant_id: UUID, item_id: UUID) -> BacklogItem:
    item = await session.get(BacklogItem, item_id)
    if item is None or item.tenant_id != tenant_id or item.deleted_at is not None:
        raise ApiError(404, "not_found", "Item not found.")
    return item


@router.get("/projects", summary="List projects")
async def list_projects(
    principal: ProjectView, request: Request, session: SessionDep
) -> JSONResponse:
    rows = (
        await session.execute(
            select(Project)
            .where(Project.tenant_id == principal.tenant_id, Project.status == "ACTIVE")
            .order_by(Project.name)
        )
    ).scalars()
    return JSONResponse(envelope(request, [project_payload(project) for project in rows]))


@router.post("/projects", status_code=201, summary="Create a project")
async def create_project(
    payload: ProjectCreate, principal: ProjectCreatePerm, request: Request, session: SessionDep
) -> JSONResponse:
    existing = (
        await session.execute(
            select(Project.id).where(
                Project.tenant_id == principal.tenant_id, Project.key == payload.key
            )
        )
    ).first()
    if existing is not None:
        raise ApiError(409, "duplicate_key", "That project key is already in use.")
    project = Project(
        id=new_id(),
        tenant_id=principal.tenant_id,
        key=payload.key,
        name=payload.name,
        description=payload.description,
        start_date=payload.start_date,
        end_date=payload.end_date,
        created_by=principal.user_id,
    )
    session.add(project)
    await session.commit()
    return JSONResponse(envelope(request, project_payload(project)), status_code=201)


@router.get("/projects/{project_id}", summary="Read a project")
async def read_project(
    project_id: UUID, principal: ProjectView, request: Request, session: SessionDep
) -> JSONResponse:
    project = await _project(session, principal.tenant_id, project_id)
    return JSONResponse(envelope(request, project_payload(project)))


@router.patch("/projects/{project_id}", summary="Update a project")
async def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    principal: ProjectEdit,
    request: Request,
    session: SessionDep,
) -> JSONResponse:
    project = await _project(session, principal.tenant_id, project_id)
    if payload.name is not None:
        project.name = payload.name
    if payload.description is not None:
        project.description = payload.description
    if payload.start_date is not None:
        project.start_date = payload.start_date
    if payload.end_date is not None:
        project.end_date = payload.end_date
    await session.commit()
    return JSONResponse(envelope(request, project_payload(project)))


@router.post("/projects/{project_id}/archive", summary="Archive a project")
async def archive_project(
    project_id: UUID,
    principal: ProjectArchive,
    request: Request,
    session: SessionDep,
) -> JSONResponse:
    project = await _project(session, principal.tenant_id, project_id)
    project.status = "ARCHIVED"
    await session.commit()
    return JSONResponse(envelope(request, project_payload(project)))


@router.get("/projects/{project_id}/backlog", summary="Project backlog")
async def project_backlog(
    project_id: UUID,
    principal: ItemView,
    request: Request,
    session: SessionDep,
) -> JSONResponse:
    await _project(session, principal.tenant_id, project_id)
    rows = (
        await session.execute(
            select(BacklogItem)
            .where(
                BacklogItem.tenant_id == principal.tenant_id,
                BacklogItem.project_id == project_id,
                BacklogItem.deleted_at.is_(None),
            )
            .order_by(BacklogItem.rank)
        )
    ).scalars()
    return JSONResponse(envelope(request, [item_payload(item) for item in rows]))


@router.get("/projects/{project_id}/board", summary="Board columns")
async def project_board(
    project_id: UUID,
    principal: ItemView,
    request: Request,
    session: SessionDep,
    sprint_id: UUID | None = Query(default=None),
) -> JSONResponse:
    await _project(session, principal.tenant_id, project_id)
    query = select(BacklogItem).where(
        BacklogItem.tenant_id == principal.tenant_id,
        BacklogItem.project_id == project_id,
        BacklogItem.deleted_at.is_(None),
    )
    if sprint_id:
        query = query.where(BacklogItem.sprint_id == sprint_id)
    rows = list((await session.execute(query.order_by(BacklogItem.rank))).scalars())
    columns: dict[str, list[dict[str, object]]] = {
        status: [] for status in ITEM_STATUSES
    }
    for item in rows:
        columns.setdefault(item.status, []).append(item_payload(item))
    return JSONResponse(
        envelope(
            request,
            {"columns": [{"status": key, "items": value} for key, value in columns.items()]},
        )
    )


@router.post("/backlog", status_code=201, summary="Create a backlog item")
async def create_item(
    payload: ItemCreate, principal: ItemCreatePerm, request: Request, session: SessionDep
) -> JSONResponse:
    project = await _project(session, principal.tenant_id, payload.project_id)
    if project.status != "ACTIVE":
        raise ApiError(422, "project_archived", "Archived projects are read-only.")

    sequence = (
        await session.execute(
            update(Project)
            .where(Project.id == project.id)
            .values(next_item_sequence=Project.next_item_sequence + 1)
            .returning(Project.next_item_sequence)
        )
    ).scalar_one()
    max_rank = (
        await session.execute(
            select(func.coalesce(func.max(BacklogItem.rank), 0)).where(
                BacklogItem.project_id == project.id
            )
        )
    ).scalar_one()

    item = BacklogItem(
        id=new_id(),
        tenant_id=principal.tenant_id,
        project_id=project.id,
        key=f"{project.key}-{sequence}",
        sequence=sequence,
        type=payload.type,
        title=payload.title,
        description=payload.description,
        priority=payload.priority,
        estimate_points=payload.estimate_points,
        assignee_id=payload.assignee_id,
        sprint_id=payload.sprint_id,
        rank=int(max_rank) + 1024,
        due_date=payload.due_date,
        reporter_id=principal.user_id,
        created_by=principal.user_id,
    )
    session.add(item)
    await session.commit()
    return JSONResponse(envelope(request, item_payload(item)), status_code=201)


@router.get("/backlog/{item_id}", summary="Read an item")
async def read_item(
    item_id: UUID, principal: ItemView, request: Request, session: SessionDep
) -> JSONResponse:
    item = await _item(session, principal.tenant_id, item_id)
    return JSONResponse(envelope(request, item_payload(item)))


@router.patch("/backlog/{item_id}", summary="Update an item")
async def update_item(
    item_id: UUID,
    payload: ItemUpdate,
    principal: ItemEdit,
    request: Request,
    session: SessionDep,
) -> JSONResponse:
    item = await _item(session, principal.tenant_id, item_id)
    data = payload.model_dump(exclude_unset=True)
    for field in ("title", "description", "priority", "estimate_points", "due_date", "sprint_id"):
        if field in data:
            setattr(item, field, data[field])
    if "assignee_id" in data:
        item.assignee_id = data["assignee_id"]
        history(
            session,
            item=item,
            actor_id=principal.user_id,
            field="assignee",
            before=None,
            after=str(data["assignee_id"]) if data["assignee_id"] else None,
        )
    await session.commit()
    return JSONResponse(envelope(request, item_payload(item)))


@router.post("/backlog/{item_id}/transition", summary="Change item status")
async def transition_item(
    item_id: UUID,
    payload: TransitionRequest,
    principal: ItemTransition,
    request: Request,
    session: SessionDep,
) -> JSONResponse:
    item = await _item(session, principal.tenant_id, item_id)
    if payload.status == item.status:
        return JSONResponse(envelope(request, item_payload(item)))
    if payload.status not in TRANSITIONS.get(item.status, set()):
        raise ApiError(
            422, "invalid_transition", f"Cannot move from {item.status} to {payload.status}."
        )
    before = item.status
    item.status = payload.status
    item.completed_at = datetime.now(tz=UTC) if payload.status == "DONE" else None
    history(
        session,
        item=item,
        actor_id=principal.user_id,
        field="status",
        before=before,
        after=payload.status,
    )
    await session.commit()
    return JSONResponse(envelope(request, item_payload(item)))


@router.post("/backlog/{item_id}/assign", summary="Assign or unassign")
async def assign_item(
    item_id: UUID,
    payload: AssignRequest,
    principal: ItemAssign,
    request: Request,
    session: SessionDep,
) -> JSONResponse:
    item = await _item(session, principal.tenant_id, item_id)
    before = str(item.assignee_id) if item.assignee_id else None
    item.assignee_id = payload.assignee_id
    history(
        session,
        item=item,
        actor_id=principal.user_id,
        field="assignee",
        before=before,
        after=str(payload.assignee_id) if payload.assignee_id else None,
    )
    await session.commit()
    return JSONResponse(envelope(request, item_payload(item)))


@router.post("/backlog/{item_id}/rank", summary="Move an item in the backlog")
async def rank_item(
    item_id: UUID,
    payload: RankRequest,
    principal: ItemEdit,
    request: Request,
    session: SessionDep,
) -> JSONResponse:
    item = await _item(session, principal.tenant_id, item_id)
    before_item = (
        await _item(session, principal.tenant_id, payload.before_item_id)
        if payload.before_item_id
        else None
    )
    after_item = (
        await _item(session, principal.tenant_id, payload.after_item_id)
        if payload.after_item_id
        else None
    )

    low = before_item.rank if before_item else 0
    high = after_item.rank if after_item else low + 2048
    if high - low < 2:
        rows = list(
            (
                await session.execute(
                    select(BacklogItem)
                    .where(
                        BacklogItem.project_id == item.project_id,
                        BacklogItem.deleted_at.is_(None),
                    )
                    .order_by(BacklogItem.rank)
                )
            ).scalars()
        )
        for index, row in enumerate(rows):
            if row.id != item.id:
                row.rank = (index + 1) * 1024
        low, high = 0, 1024 * (len(rows) + 1)
    item.rank = (low + high) // 2
    await session.commit()
    return JSONResponse(envelope(request, item_payload(item)))


@router.get("/backlog/{item_id}/comments", summary="List comments")
async def list_comments(
    item_id: UUID, principal: ItemView, request: Request, session: SessionDep
) -> JSONResponse:
    await _item(session, principal.tenant_id, item_id)
    rows = (
        await session.execute(
            select(ItemComment)
            .where(ItemComment.item_id == item_id, ItemComment.deleted_at.is_(None))
            .order_by(ItemComment.created_at)
        )
    ).scalars()
    return JSONResponse(
        envelope(
            request,
            [
                {
                    "id": str(row.id),
                    "author_id": str(row.author_id),
                    "body": row.body,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ],
        )
    )


@router.post("/backlog/{item_id}/comments", status_code=201, summary="Add a comment")
async def add_comment(
    item_id: UUID,
    payload: CommentCreate,
    principal: ItemCommentPerm,
    request: Request,
    session: SessionDep,
) -> JSONResponse:
    await _item(session, principal.tenant_id, item_id)
    comment = ItemComment(
        id=new_id(),
        tenant_id=principal.tenant_id,
        item_id=item_id,
        author_id=principal.user_id,
        body=payload.body,
    )
    session.add(comment)
    await session.commit()
    return JSONResponse(
        envelope(request, {"id": str(comment.id), "body": comment.body}), status_code=201
    )


@router.get("/sprints", summary="List sprints")
async def list_sprints(
    principal: SprintView,
    request: Request,
    session: SessionDep,
    project_id: UUID = Query(...),
) -> JSONResponse:
    await _project(session, principal.tenant_id, project_id)
    rows = (
        await session.execute(
            select(Sprint).where(Sprint.project_id == project_id).order_by(Sprint.created_at)
        )
    ).scalars()
    return JSONResponse(envelope(request, [sprint_payload(sprint) for sprint in rows]))


@router.post("/sprints", status_code=201, summary="Create a sprint")
async def create_sprint(
    payload: SprintCreate, principal: SprintManage, request: Request, session: SessionDep
) -> JSONResponse:
    await _project(session, principal.tenant_id, payload.project_id)
    sprint = Sprint(
        id=new_id(),
        tenant_id=principal.tenant_id,
        project_id=payload.project_id,
        name=payload.name,
        goal=payload.goal,
        start_date=payload.start_date,
        end_date=payload.end_date,
    )
    session.add(sprint)
    await session.commit()
    return JSONResponse(envelope(request, sprint_payload(sprint)), status_code=201)


@router.get("/sprints/{sprint_id}/items", summary="Items committed to a sprint")
async def sprint_items(
    sprint_id: UUID, principal: SprintView, request: Request, session: SessionDep
) -> JSONResponse:
    sprint = await session.get(Sprint, sprint_id)
    if sprint is None or sprint.tenant_id != principal.tenant_id:
        raise ApiError(404, "not_found", "Sprint not found.")
    rows = (
        await session.execute(
            select(BacklogItem)
            .where(
                BacklogItem.sprint_id == sprint_id,
                BacklogItem.tenant_id == principal.tenant_id,
                BacklogItem.deleted_at.is_(None),
            )
            .order_by(BacklogItem.rank)
        )
    ).scalars()
    return JSONResponse(envelope(request, [item_payload(item) for item in rows]))


@router.post("/sprints/{sprint_id}/start", summary="Start a sprint")
async def start_sprint(
    sprint_id: UUID, principal: SprintManage, request: Request, session: SessionDep
) -> JSONResponse:
    sprint = await session.get(Sprint, sprint_id)
    if sprint is None or sprint.tenant_id != principal.tenant_id:
        raise ApiError(404, "not_found", "Sprint not found.")
    active = (
        await session.execute(
            select(Sprint.id).where(
                Sprint.project_id == sprint.project_id, Sprint.state == "ACTIVE"
            )
        )
    ).first()
    if active is not None:
        raise ApiError(409, "active_sprint_exists", "A sprint is already active.")
    total = (
        await session.execute(
            select(func.coalesce(func.sum(BacklogItem.estimate_points), 0)).where(
                BacklogItem.sprint_id == sprint.id, BacklogItem.deleted_at.is_(None)
            )
        )
    ).scalar_one()
    sprint.state = "ACTIVE"
    sprint.started_at = datetime.now(tz=UTC)
    sprint.committed_points = total
    await session.commit()
    return JSONResponse(envelope(request, sprint_payload(sprint)))


@router.post("/sprints/{sprint_id}/complete", summary="Complete a sprint")
async def complete_sprint(
    sprint_id: UUID, principal: SprintManage, request: Request, session: SessionDep
) -> JSONResponse:
    sprint = await session.get(Sprint, sprint_id)
    if sprint is None or sprint.tenant_id != principal.tenant_id:
        raise ApiError(404, "not_found", "Sprint not found.")
    sprint.state = "COMPLETED"
    sprint.completed_at = datetime.now(tz=UTC)
    carried = (
        await session.execute(
            update(BacklogItem)
            .where(BacklogItem.sprint_id == sprint.id, BacklogItem.status != "DONE")
            .values(sprint_id=None)
        )
    ).rowcount  # type: ignore[attr-defined]
    await session.commit()
    return JSONResponse(
        envelope(request, {**sprint_payload(sprint), "carried_over": carried})
    )


@router.post("/sprints/{sprint_id}/items", summary="Add items to a sprint")
async def add_sprint_items(
    sprint_id: UUID,
    payload: SprintItemsRequest,
    principal: SprintManage,
    request: Request,
    session: SessionDep,
) -> JSONResponse:
    sprint = await session.get(Sprint, sprint_id)
    if sprint is None or sprint.tenant_id != principal.tenant_id:
        raise ApiError(404, "not_found", "Sprint not found.")
    updated = (
        await session.execute(
            update(BacklogItem)
            .where(
                BacklogItem.id.in_(payload.item_ids),
                BacklogItem.project_id == sprint.project_id,
                BacklogItem.deleted_at.is_(None),
            )
            .values(sprint_id=sprint.id)
        )
    ).rowcount  # type: ignore[attr-defined]
    await session.commit()
    return JSONResponse(envelope(request, {"updated": updated}))


@router.delete("/sprints/{sprint_id}/items/{item_id}", status_code=204, summary="Remove an item")
async def remove_sprint_item(
    sprint_id: UUID,
    item_id: UUID,
    principal: SprintManage,
    request: Request,
    session: SessionDep,
) -> Response:
    item = await _item(session, principal.tenant_id, item_id)
    if item.sprint_id == sprint_id:
        item.sprint_id = None
        await session.commit()
    return Response(status_code=204)


async def _service_guard(
    request: Request, x_service_token: Annotated[str | None, Header()] = None
) -> None:
    expected = request.app.state.settings.bootstrap_token
    if not expected or not x_service_token or not secrets.compare_digest(
        x_service_token, expected
    ):
        raise ApiError(403, "permission_denied", "Invalid service token.")


ServiceDep = Annotated[None, Depends(_service_guard)]


@internal_router.get("/items/{item_id}", summary="Internal item lookup")
async def internal_item(
    item_id: UUID,
    tenant_id: UUID,
    _: ServiceDep,
    request: Request,
    session: SessionDep,
) -> JSONResponse:
    item = await session.get(BacklogItem, item_id)
    if item is None or item.tenant_id != tenant_id or item.deleted_at is not None:
        raise ApiError(404, "not_found", "Item not found.")
    return JSONResponse(
        envelope(
            request,
            {
                "id": str(item.id),
                "key": item.key,
                "project_id": str(item.project_id),
                "title": item.title,
                "status": item.status,
                "assignee_id": str(item.assignee_id) if item.assignee_id else None,
            },
        )
    )
