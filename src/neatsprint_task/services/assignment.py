"""Assignment rules that keep work inside a project's teams."""

from __future__ import annotations

from uuid import UUID

from fastapi import Request
from neatsprint_lib.errors import ApiError
from sqlalchemy.ext.asyncio import AsyncSession

from neatsprint_task.services import access, iam_client

INVALID_ASSIGNEE = "That user cannot be assigned to this project."


async def ensure_assignable(
    session: AsyncSession,
    request: Request,
    tenant_id: UUID,
    project_id: UUID,
    assignee_id: UUID | None,
) -> None:
    """Raise 422 unless the assignee is active and belongs to the project's teams.

    A project with no linked teams is open to the tenant, so any active member may be
    assigned. Team lookups that cannot be completed return 503 rather than pretending
    the user is an outsider.
    """
    if assignee_id is None:
        return
    summary = (await iam_client.lookup_users(request, tenant_id, [assignee_id])).get(
        assignee_id
    )
    if summary is None or summary.status != "ACTIVE":
        raise ApiError(422, "assignee_not_eligible", INVALID_ASSIGNEE)
    linked = await access.project_team_ids(session, tenant_id, project_id)
    if not linked:
        return
    member_teams, unknown = await access.member_team_ids_for_user(
        request, tenant_id, linked, assignee_id
    )
    if linked.isdisjoint(member_teams):
        if unknown:
            raise ApiError(
                503, "dependency_unavailable", "Could not verify the assignee right now."
            )
        raise ApiError(422, "assignee_not_eligible", INVALID_ASSIGNEE)
