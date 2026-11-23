from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest

from neatsprint_task.services import access

TENANT_ID: UUID = uuid4()
TEAM_A: UUID = uuid4()
TEAM_B: UUID = uuid4()
USER_ID: UUID = uuid4()


def test_unlinked_project_is_visible_to_everyone() -> None:
    assert access.allows_project_access(
        linked_team_ids=set(), member_team_ids=set(), can_manage=False
    )


def test_manager_permission_bypasses_team_mapping() -> None:
    assert access.allows_project_access(
        linked_team_ids={TEAM_A}, member_team_ids=set(), can_manage=True
    )


def test_team_member_is_allowed() -> None:
    assert access.allows_project_access(
        linked_team_ids={TEAM_A, TEAM_B}, member_team_ids={TEAM_B}, can_manage=False
    )


def test_non_member_is_denied() -> None:
    assert not access.allows_project_access(
        linked_team_ids={TEAM_A}, member_team_ids={TEAM_B}, can_manage=False
    )


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    access.clear_membership_cache()
    yield
    access.clear_membership_cache()


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    team_internal_url="http://team:8000",
                    service_token="svc-token",
                    bootstrap_token="boot-token",
                )
            )
        )
    )


async def test_team_members_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["token"] = request.headers.get("x-service-token")
        return httpx.Response(200, json={"data": {"user_ids": [str(USER_ID)]}})

    monkeypatch.setattr(
        access,
        "_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    members = await access.fetch_team_members(_request(), TENANT_ID, TEAM_A)  # type: ignore[arg-type]

    assert members == frozenset({USER_ID})
    assert seen["url"] == (
        f"http://team:8000/internal/teams/{TEAM_A}/members?tenant_id={TENANT_ID}"
    )
    assert seen["token"] == "svc-token"


async def test_team_members_malformed_response(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"user_ids": ["not-a-uuid"]}})

    monkeypatch.setattr(
        access,
        "_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert await access.fetch_team_members(_request(), TENANT_ID, TEAM_A) is None  # type: ignore[arg-type]


async def test_team_members_are_cached_per_team(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[UUID] = []

    async def fake_fetch(request: object, tenant_id: UUID, team_id: UUID) -> frozenset[UUID]:
        calls.append(team_id)
        return frozenset({USER_ID})

    monkeypatch.setattr(access, "fetch_team_members", fake_fetch)

    first = await access.team_members(None, TENANT_ID, TEAM_A)  # type: ignore[arg-type]
    second = await access.team_members(None, TENANT_ID, TEAM_A)  # type: ignore[arg-type]

    assert first == second == frozenset({USER_ID})
    assert calls == [TEAM_A]


async def test_team_members_cache_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[UUID] = []
    clock = [1000.0]

    async def fake_fetch(request: object, tenant_id: UUID, team_id: UUID) -> frozenset[UUID]:
        calls.append(team_id)
        return frozenset({USER_ID})

    monkeypatch.setattr(access, "fetch_team_members", fake_fetch)
    monkeypatch.setattr(access, "_now", lambda: clock[0])

    await access.team_members(None, TENANT_ID, TEAM_A)  # type: ignore[arg-type]
    clock[0] += access.CACHE_TTL_SECONDS + 1
    await access.team_members(None, TENANT_ID, TEAM_A)  # type: ignore[arg-type]

    assert len(calls) == 2


async def test_unknown_membership_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[UUID] = []

    async def failing_fetch(
        request: object, tenant_id: UUID, team_id: UUID
    ) -> frozenset[UUID] | None:
        calls.append(team_id)
        return None

    monkeypatch.setattr(access, "fetch_team_members", failing_fetch)

    first = await access.team_members(None, TENANT_ID, TEAM_A)  # type: ignore[arg-type]
    second = await access.team_members(None, TENANT_ID, TEAM_A)  # type: ignore[arg-type]

    assert first is None and second is None
    assert len(calls) == 2
