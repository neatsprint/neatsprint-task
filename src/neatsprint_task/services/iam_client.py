"""Minimal IAM client used to validate assignees.

The Task service must not trust a bare user id: it asks IAM for a short summary and
requires the user to be active. Lookups are batched, bounded by a 3 s timeout, cached
for 60 s, and protected by a simple circuit breaker so an IAM outage does not turn
every assignment into a multi-second wait.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

import httpx
from fastapi import Request
from neatsprint_lib.errors import ApiError

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60.0
FAILURE_THRESHOLD = 5
OPEN_SECONDS = 30.0

UNAVAILABLE = "Could not verify the assignee right now."


@dataclass(frozen=True, slots=True)
class UserSummary:
    id: UUID
    email: str
    full_name: str
    status: str


class _Circuit:
    failures: int = 0
    open_until: float = 0.0


_cache: dict[tuple[str, str], tuple[float, UserSummary]] = {}
_circuit = _Circuit()


def _now() -> float:
    return time.monotonic()


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=3)


def clear_user_cache() -> None:
    _cache.clear()


def reset_circuit() -> None:
    _circuit.failures = 0
    _circuit.open_until = 0.0


def _record_failure() -> None:
    _circuit.failures += 1
    if _circuit.failures >= FAILURE_THRESHOLD:
        _circuit.open_until = _now() + OPEN_SECONDS


def _record_success() -> None:
    _circuit.failures = 0
    _circuit.open_until = 0.0


async def fetch_users(
    request: Request, tenant_id: UUID, user_ids: set[UUID]
) -> dict[UUID, UserSummary] | None:
    settings = request.app.state.settings
    token = settings.service_token or settings.bootstrap_token
    if not token:
        logger.warning("iam_lookup_token_missing")
        return None
    try:
        async with _http_client() as client:
            response = await client.post(
                f"{settings.iam_internal_url}/internal/users/lookup",
                json={
                    "tenant_id": str(tenant_id),
                    "user_ids": [str(user_id) for user_id in sorted(user_ids, key=str)],
                },
                headers={"X-Bootstrap-Token": token},
            )
    except httpx.HTTPError:
        _record_failure()
        logger.warning("iam_lookup_unavailable")
        return None
    if response.status_code != 200:
        _record_failure()
        logger.warning("iam_lookup_failed", extra={"status": response.status_code})
        return None
    payload = response.json().get("data")
    if not isinstance(payload, list):
        _record_failure()
        logger.warning("iam_lookup_malformed")
        return None
    summaries: dict[UUID, UserSummary] = {}
    try:
        for item in payload:
            summary = UserSummary(
                id=UUID(str(item["id"])),
                email=str(item["email"]),
                full_name=str(item["full_name"]),
                status=str(item["status"]),
            )
            summaries[summary.id] = summary
    except (KeyError, TypeError, ValueError):
        _record_failure()
        logger.warning("iam_lookup_malformed")
        return None
    _record_success()
    return summaries


async def lookup_users(
    request: Request, tenant_id: UUID, user_ids: Iterable[UUID]
) -> dict[UUID, UserSummary]:
    wanted = set(user_ids)
    if not wanted:
        return {}
    now = _now()
    found: dict[UUID, UserSummary] = {}
    missing: set[UUID] = set()
    for user_id in wanted:
        cached = _cache.get((str(tenant_id), str(user_id)))
        if cached is not None and cached[0] > now:
            found[user_id] = cached[1]
        else:
            missing.add(user_id)
    if missing:
        if _circuit.open_until > now:
            raise ApiError(503, "dependency_unavailable", UNAVAILABLE)
        fetched = await fetch_users(request, tenant_id, missing)
        if fetched is None:
            raise ApiError(503, "dependency_unavailable", UNAVAILABLE)
        for user_id, summary in fetched.items():
            _cache[(str(tenant_id), str(user_id))] = (
                now + CACHE_TTL_SECONDS,
                summary,
            )
            found[user_id] = summary
    return found
