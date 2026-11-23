"""Project access resolution for team-scoped projects.

A project is open to every tenant member when no teams are linked to it. When
teams are linked, only members of at least one linked team may access it. Users
who can create projects (manager, admin, owner) always retain access so they can
administer work they did not create.

Team membership lives in the Team service and is read through its internal API.
Results are cached for a short TTL because list and item endpoints resolve
access on every request.
"""

from __future__ import annotations

import logging
import time
from uuid import UUID

import httpx
from fastapi import Request
from neatsprint_lib.auth import Principal
from neatsprint_lib.errors import ApiError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from neatsprint_task.db.models import BacklogItem, Project, ProjectTeam, Sprint

logger = logging.getLogger(__name__)

MANAGE_PERMISSION = "task:project:create"
CACHE_TTL_SECONDS = 60.0

_member_cache: dict[tuple[str, str], tuple[float, frozenset[UUID]]] = {}


def allows_project_access(
    *,
    linked_team_ids: set[UUID],
    member_team_ids: set[UUID],
    can_manage: bool,
) -> bool:
    """Decide access without performing I/O (also the unit under test)."""
    if can_manage or not linked_team_ids:
        return True
    return not linked_team_ids.isdisjoint(member_team_ids)


def _now() -> float:
    return time.monotonic()


def clear_membership_cache() -> None:
    _member_cache.clear()


def clear_team_membership(tenant_id: UUID | str, team_id: UUID | str) -> bool:
    """Drop one cached membership set. Returns whether an entry existed."""
    return _member_cache.pop((str(tenant_id), str(team_id)), None) is not None


async def fetch_team_members(
    request: Request, tenant_id: UUID, team_id: UUID
) -> frozenset[UUID] | None:
    """Call the Team service. Returns ``None`` when membership is unknown."""
    settings = request.app.state.settings
    token = settings.service_token or settings.bootstrap_token
    if not token:
        logger.warning("team_service_token_missing", extra={"team_id": str(team_id)})
        return None
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(
                f"{settings.team_internal_url}/internal/teams/{team_id}/members",
                params={"tenant_id": str(tenant_id)},
                headers={"X-Service-Token": token},
            )
    except httpx.HTTPError:
        logger.warning("team_members_unavailable", extra={"team_id": str(team_id)})
        return None
    if response.status_code != 200:
        logger.warning(
            "team_members_failed",
            extra={"team_id": str(team_id), "status": response.status_code},
        )
        return None
    payload = response.json().get("data")
    user_ids = payload.get("user_ids") if isinstance(payload, dict) else None
    if not isinstance(user_ids, list):
        return None
    try:
        return frozenset(UUID(str(user_id)) for user_id in user_ids)
    except (TypeError, ValueError):
        logger.warning("team_members_malformed", extra={"team_id": str(team_id)})
        return None


async def team_members(
    request: Request, tenant_id: UUID, team_id: UUID
) -> frozenset[UUID] | None:
    key = (str(tenant_id), str(team_id))
    cached = _member_cache.get(key)
    now = _now()
    if cached is not None and cached[0] > now:
        return cached[1]
    members = await fetch_team_members(request, tenant_id, team_id)
    if members is not None:
        _member_cache[key] = (now + CACHE_TTL_SECONDS, members)
    return members


async def project_team_ids(
    session: AsyncSession, tenant_id: UUID, project_id: UUID
) -> set[UUID]:
    rows = (
        await session.execute(
            select(ProjectTeam.team_id).where(
                ProjectTeam.project_id == project_id,
                ProjectTeam.tenant_id == tenant_id,
            )
        )
    ).scalars()
    return set(rows)


async def _member_team_ids(
    request: Request, tenant_id: UUID, team_ids: set[UUID], user_id: UUID
) -> set[UUID]:
    member_teams: set[UUID] = set()
    for team_id in team_ids:
        members = await team_members(request, tenant_id, team_id)
        if members is not None and user_id in members:
            member_teams.add(team_id)
    return member_teams


async def can_access_project(
    session: AsyncSession, request: Request, principal: Principal, project_id: UUID
) -> bool:
    if principal.can(MANAGE_PERMISSION):
        return True
    linked = await project_team_ids(session, principal.tenant_id, project_id)
    if not linked:
        return True
    member_teams = await _member_team_ids(
        request, principal.tenant_id, linked, principal.user_id
    )
    return allows_project_access(
        linked_team_ids=linked,
        member_team_ids=member_teams,
        can_manage=False,
    )


async def accessible_project(
    session: AsyncSession, request: Request, principal: Principal, project_id: UUID
) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.tenant_id != principal.tenant_id:
        raise ApiError(404, "not_found", "Project not found.")
    if not await can_access_project(session, request, principal, project.id):
        raise ApiError(404, "not_found", "Project not found.")
    return project


async def accessible_item(
    session: AsyncSession, request: Request, principal: Principal, item_id: UUID
) -> BacklogItem:
    item = await session.get(BacklogItem, item_id)
    if item is None or item.tenant_id != principal.tenant_id or item.deleted_at is not None:
        raise ApiError(404, "not_found", "Item not found.")
    if not await can_access_project(session, request, principal, item.project_id):
        raise ApiError(404, "not_found", "Item not found.")
    return item


async def accessible_sprint(
    session: AsyncSession, request: Request, principal: Principal, sprint_id: UUID
) -> Sprint:
    sprint = await session.get(Sprint, sprint_id)
    if sprint is None or sprint.tenant_id != principal.tenant_id:
        raise ApiError(404, "not_found", "Sprint not found.")
    if not await can_access_project(session, request, principal, sprint.project_id):
        raise ApiError(404, "not_found", "Sprint not found.")
    return sprint


async def accessible_projects(
    session: AsyncSession, request: Request, principal: Principal
) -> list[Project]:
    """Active projects the principal may see, ordered by name."""
    query = (
        select(Project)
        .where(Project.tenant_id == principal.tenant_id, Project.status == "ACTIVE")
        .order_by(Project.name)
    )
    projects = list((await session.execute(query)).scalars())
    if principal.can(MANAGE_PERMISSION) or not projects:
        return projects
    mappings = (
        await session.execute(
            select(ProjectTeam.project_id, ProjectTeam.team_id).where(
                ProjectTeam.tenant_id == principal.tenant_id,
                ProjectTeam.project_id.in_([project.id for project in projects]),
            )
        )
    ).all()
    if not mappings:
        return projects
    linked_by_project: dict[UUID, set[UUID]] = {}
    for project_id, team_id in mappings:
        linked_by_project.setdefault(project_id, set()).add(team_id)
    member_teams = await _member_team_ids(
        request,
        principal.tenant_id,
        {team_id for _, team_id in mappings},
        principal.user_id,
    )
    visible: list[Project] = []
    for project in projects:
        linked = linked_by_project.get(project.id, set())
        if allows_project_access(
            linked_team_ids=linked,
            member_team_ids=member_teams,
            can_manage=False,
        ):
            visible.append(project)
    return visible
