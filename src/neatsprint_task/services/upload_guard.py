"""Upload validation: magic-byte sniffing and optional ClamAV scanning."""

from __future__ import annotations

import asyncio
import socket
import struct

from neatsprint_lib.errors import ApiError

MAGIC: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"RIFF", "image/webp"),
    (b"%PDF-", "application/pdf"),
    (b"PK\x03\x04", "application/zip"),
]


def detect_content_type(data: bytes) -> str | None:
    for prefix, content_type in MAGIC:
        if data.startswith(prefix):
            if content_type == "image/webp" and data[8:12] != b"WEBP":
                continue
            return content_type
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if "\x00" not in text:
        return "text/plain"
    return None


def validate_upload(
    data: bytes,
    declared: str,
    *,
    allowed: set[str],
    max_bytes: int,
) -> str:
    if not data:
        raise ApiError(422, "validation_failed", "The file is empty.")
    if len(data) > max_bytes:
        raise ApiError(413, "payload_too_large", "The file is too large.")
    if declared not in allowed:
        raise ApiError(415, "unsupported_media_type", "That file type is not supported.")
    detected = detect_content_type(data)
    text_types = {"text/plain", "text/csv"}
    if detected is None:
        raise ApiError(415, "unsupported_media_type", "The file content is not recognised.")
    if detected != declared and not (detected == "text/plain" and declared in text_types):
        raise ApiError(
            415,
            "content_mismatch",
            "The file content does not match its declared type.",
        )
    return detected


def _scan(host: str, port: int, data: bytes) -> bool:
    with socket.create_connection((host, port), timeout=10) as connection:
        connection.sendall(b"zINSTREAM\0")
        for offset in range(0, len(data), 65536):
            chunk = data[offset : offset + 65536]
            connection.sendall(struct.pack("!I", len(chunk)) + chunk)
        connection.sendall(struct.pack("!I", 0))
        response = connection.recv(4096)
    return b"OK" in response


async def virus_scan(settings: object, data: bytes) -> None:
    host = getattr(settings, "clamav_host", None)
    if not host:
        return
    port = int(getattr(settings, "clamav_port", 3310))
    try:
        clean = await asyncio.to_thread(_scan, host, port, data)
    except OSError as exc:
        raise ApiError(
            503, "dependency_unavailable", "The malware scanner is unavailable."
        ) from exc
    if not clean:
        raise ApiError(422, "malware_detected", "The file was rejected by the malware scanner.")
