from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest

from neatsprint_task.services import iam_client

TENANT_ID: UUID = uuid4()
USER_ID: UUID = uuid4()


def _request(**settings: str) -> SimpleNamespace:
    values = {
        "service_token": "svc-token",
        "bootstrap_token": "boot-token",
        "iam_internal_url": "http://iam:8000",
    }
    values.update(settings)
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(**values)))
    )


@pytest.fixture(autouse=True)
def _reset() -> None:
    iam_client.clear_user_cache()
    iam_client.reset_circuit()
    yield
    iam_client.clear_user_cache()
    iam_client.reset_circuit()


async def test_lookup_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["token"] = request.headers.get("x-bootstrap-token")
        seen["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": str(USER_ID),
                        "email": "member@example.com",
                        "full_name": "Mia Member",
                        "status": "ACTIVE",
                    }
                ]
            },
        )

    monkeypatch.setattr(
        iam_client,
        "_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = await iam_client.lookup_users(_request(), TENANT_ID, [USER_ID])  # type: ignore[arg-type]

    assert result[USER_ID].email == "member@example.com"
    assert result[USER_ID].status == "ACTIVE"
    assert seen["url"] == "http://iam:8000/internal/users/lookup"
    assert seen["token"] == "svc-token"
    assert str(USER_ID) in str(seen["body"])
    assert str(TENANT_ID) in str(seen["body"])


async def test_lookup_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": str(USER_ID),
                        "email": "member@example.com",
                        "full_name": "Mia Member",
                        "status": "ACTIVE",
                    }
                ]
            },
        )

    monkeypatch.setattr(
        iam_client,
        "_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await iam_client.lookup_users(_request(), TENANT_ID, [USER_ID])  # type: ignore[arg-type]
    await iam_client.lookup_users(_request(), TENANT_ID, [USER_ID])  # type: ignore[arg-type]

    assert len(calls) == 1


async def test_lookup_cache_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    clock = [1000.0]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": str(USER_ID),
                        "email": "member@example.com",
                        "full_name": "Mia Member",
                        "status": "ACTIVE",
                    }
                ]
            },
        )

    monkeypatch.setattr(iam_client, "_now", lambda: clock[0])
    monkeypatch.setattr(
        iam_client,
        "_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await iam_client.lookup_users(_request(), TENANT_ID, [USER_ID])  # type: ignore[arg-type]
    clock[0] += iam_client.CACHE_TTL_SECONDS + 1
    await iam_client.lookup_users(_request(), TENANT_ID, [USER_ID])  # type: ignore[arg-type]

    assert len(calls) == 2


async def test_identity_unavailable_opens_circuit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    clock = [1000.0]
    failing = [True]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if failing[0]:
            raise httpx.ConnectError("iam down", request=request)
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(iam_client, "_now", lambda: clock[0])
    monkeypatch.setattr(
        iam_client,
        "_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    for _ in range(iam_client.FAILURE_THRESHOLD):
        with pytest.raises(Exception) as error:
            await iam_client.lookup_users(_request(), TENANT_ID, [uuid4()])  # type: ignore[arg-type]
        assert getattr(error.value, "status", None) == 503

    closed_calls = len(calls)
    with pytest.raises(Exception) as error:
        await iam_client.lookup_users(_request(), TENANT_ID, [uuid4()])  # type: ignore[arg-type]
    assert getattr(error.value, "status", None) == 503
    assert len(calls) == closed_calls

    clock[0] += iam_client.OPEN_SECONDS + 1
    failing[0] = False
    result = await iam_client.lookup_users(_request(), TENANT_ID, [uuid4()])  # type: ignore[arg-type]
    assert result == {}


async def test_malformed_response_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "not-a-uuid"}]})

    monkeypatch.setattr(
        iam_client,
        "_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(Exception) as error:
        await iam_client.lookup_users(_request(), TENANT_ID, [USER_ID])  # type: ignore[arg-type]
    assert getattr(error.value, "status", None) == 503
