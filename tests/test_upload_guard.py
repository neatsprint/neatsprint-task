from __future__ import annotations

import pytest
from neatsprint_lib.errors import ApiError

from neatsprint_task.services.upload_guard import detect_content_type, validate_upload

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PDF = b"%PDF-1.7\n" + b"x" * 16


def test_detects_png_and_pdf() -> None:
    assert detect_content_type(PNG) == "image/png"
    assert detect_content_type(PDF) == "application/pdf"


def test_accepts_matching_type() -> None:
    assert (
        validate_upload(
            PNG, "image/png", allowed={"image/png"}, max_bytes=1024
        )
        == "image/png"
    )


def test_rejects_content_mismatch() -> None:
    with pytest.raises(ApiError) as error:
        validate_upload(PDF, "image/png", allowed={"image/png"}, max_bytes=1024)
    assert error.value.code == "content_mismatch"


def test_rejects_oversize_and_empty() -> None:
    with pytest.raises(ApiError) as error:
        validate_upload(PNG, "image/png", allowed={"image/png"}, max_bytes=4)
    assert error.value.status == 413
    with pytest.raises(ApiError) as error:
        validate_upload(b"", "image/png", allowed={"image/png"}, max_bytes=1024)
    assert error.value.status == 422
