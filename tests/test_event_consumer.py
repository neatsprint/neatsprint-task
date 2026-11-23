from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest

from neatsprint_task.services import access, event_consumer

USER_ID = uuid4()


async def _prime_cache(monkeypatch: pytest.MonkeyPatch, tenant_id: UUID, team_id: UUID) -> None:
    async def fake_fetch(request: object, tenant: UUID, team: UUID) -> frozenset[UUID]:
        return frozenset({USER_ID})

    monkeypatch.setattr(access, "fetch_team_members", fake_fetch)
    assert await access.team_members(None, tenant_id, team_id) == frozenset({USER_ID})  # type: ignore[arg-type]


def _event(event_type: str, tenant_id: UUID, team_id: UUID) -> dict[str, str]:
    return {
        "event_type": event_type,
        "tenant_id": str(tenant_id),
        "data": json.dumps({"team_id": str(team_id), "user_id": str(uuid4())}),
    }


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    access.clear_membership_cache()
    yield
    access.clear_membership_cache()


async def test_member_added_invalidates_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id, team_id = uuid4(), uuid4()
    await _prime_cache(monkeypatch, tenant_id, team_id)

    assert event_consumer.handle_event(_event("team.member_added", tenant_id, team_id)) is True

    calls: list[UUID] = []

    async def refreshed(request: object, tenant: UUID, team: UUID) -> frozenset[UUID]:
        calls.append(team)
        return frozenset()

    monkeypatch.setattr(access, "fetch_team_members", refreshed)
    assert await access.team_members(None, tenant_id, team_id) == frozenset()  # type: ignore[arg-type]
    assert calls == [team_id]


async def test_unrelated_event_keeps_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id, team_id = uuid4(), uuid4()
    await _prime_cache(monkeypatch, tenant_id, team_id)

    assert event_consumer.handle_event(_event("item.created", tenant_id, team_id)) is False

    async def must_not_run(request: object, tenant: UUID, team: UUID) -> frozenset[UUID]:
        raise AssertionError("cache should still hold the membership set")

    monkeypatch.setattr(access, "fetch_team_members", must_not_run)
    assert await access.team_members(None, tenant_id, team_id) == frozenset({USER_ID})  # type: ignore[arg-type]


async def test_malformed_event_keeps_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id, team_id = uuid4(), uuid4()
    await _prime_cache(monkeypatch, tenant_id, team_id)

    assert (
        event_consumer.handle_event(
            {"event_type": "team.member_removed", "tenant_id": str(tenant_id), "data": "{"}
        )
        is False
    )

    async def must_not_run(request: object, tenant: UUID, team: UUID) -> frozenset[UUID]:
        raise AssertionError("cache should still hold the membership set")

    monkeypatch.setattr(access, "fetch_team_members", must_not_run)
    assert await access.team_members(None, tenant_id, team_id) == frozenset({USER_ID})  # type: ignore[arg-type]
