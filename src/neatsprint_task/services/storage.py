"""S3/MinIO helpers for task attachments."""

from __future__ import annotations

import uuid
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.client import Config  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]
from fastapi import Request
from neatsprint_lib.errors import ApiError

ALLOWED_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "application/pdf",
    "text/plain",
    "text/csv",
    "application/zip",
}


def _client(request: Request, *, public: bool = False) -> Any:
    settings = request.app.state.settings
    if not settings.s3_endpoint or not settings.s3_access_key:
        raise ApiError(503, "storage_unavailable", "Object storage is not configured.")
    endpoint = (
        settings.s3_public_endpoint
        if public and settings.s3_public_endpoint
        else settings.s3_endpoint
    )
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4"),
    )


def attachment_key(tenant_id: str, item_id: str, file_name: str) -> str:
    safe = "".join(ch for ch in file_name if ch.isalnum() or ch in "._-")[:120] or "file"
    return f"tenant/{tenant_id}/items/{item_id}/{uuid.uuid4().hex}-{safe}"


def presign_put(request: Request, key: str, content_type: str) -> str:
    settings = request.app.state.settings
    try:
        return str(
            _client(request, public=True).generate_presigned_url(
                "put_object",
                Params={"Bucket": settings.s3_bucket, "Key": key, "ContentType": content_type},
                ExpiresIn=300,
            )
        )
    except (BotoCoreError, ClientError) as exc:
        raise ApiError(503, "storage_unavailable", "Could not prepare the upload.") from exc


def presign_get(request: Request, key: str) -> str:
    settings = request.app.state.settings
    try:
        return str(
            _client(request, public=True).generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.s3_bucket, "Key": key},
                ExpiresIn=300,
            )
        )
    except (BotoCoreError, ClientError) as exc:
        raise ApiError(404, "not_found", "Attachment not found.") from exc
