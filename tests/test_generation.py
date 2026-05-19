"""Tests for generation/imagen_client.py — mocked, no real API key needed."""

import base64
import struct
import sys
import zlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Pre-import so patch() can resolve the module
import generation.imagen_client as imagen_client  # noqa: E402
from parsers.base import Trend  # noqa: E402
from prompt_engine.builder import STYLE_MODIFIERS, build_prompt  # noqa: E402

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _fake_png() -> bytes:
    """Minimal valid 1×1 white PNG."""

    def chunk(name: bytes, data: bytes) -> bytes:
        c = name + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
        + chunk(b"IEND", b"")
    )


def _b64_png() -> str:
    return base64.b64encode(_fake_png()).decode()


def _make_mock_client(response: dict) -> AsyncMock:
    mock_resp = MagicMock()
    mock_resp.json.return_value = response
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)
    return mock_client


# ---------------------------------------------------------------------------
# imagen_client tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_returns_path(tmp_path):
    """generate() saves PNG file and returns a valid Path."""
    mock_client = _make_mock_client({"predictions": [{"bytesBase64Encoded": _b64_png()}]})

    mock_cfg = MagicMock()
    mock_cfg.imagen_output_dir = tmp_path
    mock_cfg.imagen_model = "imagen-4.0-fast-generate-001"
    mock_cfg.imagen_aspect_ratio = "square"
    mock_cfg.google_api_key = "test-key"

    with (
        patch.object(imagen_client, "settings", mock_cfg),
        patch.object(imagen_client.httpx, "AsyncClient", return_value=mock_client),
    ):
        result = await imagen_client.generate("sunset beach, stock photo")

    assert isinstance(result, Path)
    assert result.exists()
    assert result.suffix == ".png"
    assert result.stat().st_size > 0


@pytest.mark.asyncio
async def test_generate_posts_to_correct_url(tmp_path):
    """generate() POSTs to the Imagen endpoint with model and API key."""
    posted_url: list[str] = []

    async def fake_post(url, **kwargs):
        posted_url.append(url)
        resp = MagicMock()
        resp.json.return_value = {"predictions": [{"bytesBase64Encoded": _b64_png()}]}
        resp.raise_for_status = MagicMock()
        return resp

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = fake_post

    mock_cfg = MagicMock()
    mock_cfg.imagen_output_dir = tmp_path
    mock_cfg.imagen_model = "imagen-4.0-fast-generate-001"
    mock_cfg.imagen_aspect_ratio = "square"
    mock_cfg.google_api_key = "my-secret-key"

    with (
        patch.object(imagen_client, "settings", mock_cfg),
        patch.object(imagen_client.httpx, "AsyncClient", return_value=mock_client),
    ):
        await imagen_client.generate("test prompt")

    assert len(posted_url) == 1
    assert "imagen-4.0-fast-generate-001" in posted_url[0]
    assert "my-secret-key" in posted_url[0]


@pytest.mark.asyncio
async def test_generate_raises_on_empty_predictions(tmp_path):
    """generate() raises RuntimeError when API returns no predictions."""
    mock_client = _make_mock_client({"predictions": []})

    mock_cfg = MagicMock()
    mock_cfg.imagen_output_dir = tmp_path
    mock_cfg.imagen_model = "imagen-4.0-fast-generate-001"
    mock_cfg.imagen_aspect_ratio = "square"
    mock_cfg.google_api_key = "test-key"

    with (
        patch.object(imagen_client, "settings", mock_cfg),
        patch.object(imagen_client.httpx, "AsyncClient", return_value=mock_client),
    ):
        with pytest.raises(RuntimeError, match="no predictions"):
            await imagen_client.generate("test prompt")


@pytest.mark.asyncio
async def test_generate_aspect_ratio_passed(tmp_path):
    """generate() forwards the correct aspectRatio to the API."""
    captured_payload: list[dict] = []

    async def fake_post(url, json=None, **kwargs):
        captured_payload.append(json or {})
        resp = MagicMock()
        resp.json.return_value = {"predictions": [{"bytesBase64Encoded": _b64_png()}]}
        resp.raise_for_status = MagicMock()
        return resp

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = fake_post

    mock_cfg = MagicMock()
    mock_cfg.imagen_output_dir = tmp_path
    mock_cfg.imagen_model = "imagen-4.0-fast-generate-001"
    mock_cfg.imagen_aspect_ratio = "landscape"
    mock_cfg.google_api_key = "key"

    with (
        patch.object(imagen_client, "settings", mock_cfg),
        patch.object(imagen_client.httpx, "AsyncClient", return_value=mock_client),
    ):
        await imagen_client.generate("test", aspect_ratio="landscape")

    ratio = captured_payload[0]["parameters"]["aspectRatio"]
    assert ratio == "4:3"


# ---------------------------------------------------------------------------
# prompt_engine/builder tests
# ---------------------------------------------------------------------------


def test_build_prompt_contains_keyword():
    trend = Trend(keyword="golden gate bridge", source="shutterstock")
    prompt = build_prompt(trend)
    assert "golden gate bridge" in prompt
    assert "stock photo" in prompt
    assert "watermark" in prompt


def test_build_prompt_all_styles():
    trend = Trend(keyword="business meeting", source="shutterstock")
    for style in STYLE_MODIFIERS:
        prompt = build_prompt(trend, style=style)
        assert trend.keyword in prompt
        assert len(prompt) > 20


def test_build_prompt_unknown_style_falls_back():
    trend = Trend(keyword="mountain lake", source="adobe")
    prompt = build_prompt(trend, style="nonexistent_style")
    assert "mountain lake" in prompt
    assert "stock photo" in prompt
