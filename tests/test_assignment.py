from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from neatsprint_lib.errors import ApiError

from neatsprint_task.services import assignment, iam_client

TENANT_ID: UUID = uuid4()
PROJECT_ID: UUID = uuid4()
TEAM_ID: UUID = uuid4()
USER_ID: UUID = uuid4()


def _summary(status: str = "ACTIVE") -> iam_client.UserSummary:
    return iam_client.UserSummary(
        id=USER_ID,
        email="member@example.com",
        full_name="Mia Member",
        status=status,
    )


def _patch_lookup(
    monkeypatch: pytest.MonkeyPatch, result: dict[UUID, iam_client.UserSummary]
) -> None:
    async def fake_lookup(
        request: object, tenant_id: UUID, user_ids: object
    ) -> dict[UUID, iam_client.UserSummary]:
        return result

    monkeypatch.setattr(assignment.iam_client, "lookup_users", fake_lookup)


def _patch_teams(
    monkeypatch: pytest.MonkeyPatch, linked: set[UUID], members: set[UUID], unknown: set[UUID]
) -> None:
    async def fake_linked(session: object, tenant_id: UUID, project_id: UUID) -> set[UUID]:
        return linked

    async def fake_members(
        request: object, tenant_id: UUID, team_ids: set[UUID], user_id: UUID
    ) -> tuple[set[UUID], set[UUID]]:
        return members, unknown

    monkeypatch.setattr(assignment.access, "project_team_ids", fake_linked)
    monkeypatch.setattr(assignment.access, "member_team_ids_for_user", fake_members)


async def test_active_member_is_assignable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_lookup(monkeypatch, {USER_ID: _summary()})
    _patch_teams(monkeypatch, {TEAM_ID}, {TEAM_ID}, set())

    await assignment.ensure_assignable(object(), None, TENANT_ID, PROJECT_ID, USER_ID)  # type: ignore[arg-type]


async def test_open_project_accepts_any_active_user(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_lookup(monkeypatch, {USER_ID: _summary()})
    _patch_teams(monkeypatch, set(), set(), set())

    await assignment.ensure_assignable(object(), None, TENANT_ID, PROJECT_ID, USER_ID)  # type: ignore[arg-type]


async def test_outsider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_lookup(monkeypatch, {USER_ID: _summary()})
    _patch_teams(monkeypatch, {TEAM_ID}, set(), set())

    with pytest.raises(ApiError) as error:
        await assignment.ensure_assignable(object(), None, TENANT_ID, PROJECT_ID, USER_ID)  # type: ignore[arg-type]
    assert error.value.status == 422
    assert error.value.code == "assignee_not_eligible"


async def test_unknown_user_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_lookup(monkeypatch, {})
    _patch_teams(monkeypatch, set(), set(), set())

    with pytest.raises(ApiError) as error:
        await assignment.ensure_assignable(object(), None, TENANT_ID, PROJECT_ID, USER_ID)  # type: ignore[arg-type]
    assert error.value.status == 422
    assert error.value.code == "assignee_not_eligible"


async def test_inactive_user_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_lookup(monkeypatch, {USER_ID: _summary("DISABLED")})
    _patch_teams(monkeypatch, set(), set(), set())

    with pytest.raises(ApiError) as error:
        await assignment.ensure_assignable(object(), None, TENANT_ID, PROJECT_ID, USER_ID)  # type: ignore[arg-type]
    assert error.value.status == 422


async def test_unavailable_membership_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_lookup(monkeypatch, {USER_ID: _summary()})
    _patch_teams(monkeypatch, {TEAM_ID}, set(), {TEAM_ID})

    with pytest.raises(ApiError) as error:
        await assignment.ensure_assignable(object(), None, TENANT_ID, PROJECT_ID, USER_ID)  # type: ignore[arg-type]
    assert error.value.status == 503
    assert error.value.code == "dependency_unavailable"


async def test_unassign_skips_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def must_not_run(
        request: object, tenant_id: UUID, user_ids: object
    ) -> dict[UUID, object]:
        raise AssertionError("unassign must not call IAM")

    monkeypatch.setattr(assignment.iam_client, "lookup_users", must_not_run)

    await assignment.ensure_assignable(object(), None, TENANT_ID, PROJECT_ID, None)  # type: ignore[arg-type]
