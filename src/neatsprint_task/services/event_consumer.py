"""Consume team membership events to invalidate the project access cache.

The access resolver caches team membership for 60 seconds. Membership changes are
published to the shared event stream by the Team service; this consumer drops the
affected cache entry so the next request sees the change immediately.

The consumer group is created at ``$`` on first start. Events published before the
service first ran are intentionally skipped; the TTL still bounds staleness. A crash
between read and acknowledgment can leave a pending entry, which is harmless because
the entry expires after 60 seconds.

Replicas share one consumer group, so each event is handled by a single replica; the
others converge when their TTL expires. The MVP runs one Task replica; give each
replica its own group if immediate cross-replica invalidation is needed later.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from neatsprint_task.services.access import clear_team_membership

logger = logging.getLogger(__name__)

INVALIDATING_EVENTS = frozenset({"team.member_added", "team.member_removed"})


def handle_event(fields: dict[str, Any]) -> bool:
    """Apply one stream entry. Returns whether a cache entry was cleared."""
    if fields.get("event_type") not in INVALIDATING_EVENTS:
        return False
    tenant_id = fields.get("tenant_id")
    try:
        payload = json.loads(fields.get("data") or "{}")
    except (TypeError, ValueError):
        logger.warning("event_consumer_bad_payload")
        return False
    team_id = payload.get("team_id") if isinstance(payload, dict) else None
    if not tenant_id or not team_id:
        return False
    cleared = clear_team_membership(tenant_id, team_id)
    logger.info(
        "team_membership_cache_cleared",
        extra={"tenant_id": str(tenant_id), "team_id": str(team_id), "cached": cleared},
    )
    return cleared


async def _ensure_group(redis: Redis, stream: str, group: str) -> None:
    try:
        await redis.xgroup_create(stream, group, id="$", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def run_consumer(
    redis_url: str,
    stream: str,
    group: str,
    stop: asyncio.Event,
    *,
    block_ms: int = 2000,
    consumer: str | None = None,
) -> None:
    """Background loop; one consumer per process is sufficient for cache invalidation."""
    redis = Redis.from_url(redis_url, decode_responses=True)
    name = consumer or f"task-{uuid.uuid4().hex[:8]}"
    try:
        while not stop.is_set():
            try:
                await _ensure_group(redis, stream, group)
                break
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - Redis may not be ready yet
                logger.exception("event_consumer_group_failed")
                await asyncio.sleep(2)
        while not stop.is_set():
            try:
                batches = await redis.xreadgroup(
                    group, name, {stream: ">"}, count=100, block=block_ms
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the consumer must survive transient failures
                logger.exception("event_consumer_read_failed")
                await asyncio.sleep(2)
                continue
            for _, messages in batches or []:
                for message_id, fields in messages:
                    try:
                        handle_event(fields)
                    finally:
                        await redis.xack(stream, group, message_id)
    finally:
        await redis.aclose()
