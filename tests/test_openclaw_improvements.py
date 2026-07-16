"""Tests for magic bytes MIME detection, typing renewal, and streaming throttle."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import adapter


class TestDetectImageMime:
    """Tests for _detect_image_mime magic bytes detection."""

    def test_png(self):
        # PNG magic bytes: 89 50 4E 47 0D 0A 1A 0A
        data = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        result = adapter.MaxAdapter._detect_image_mime(data)
        assert result == "image/png"

    def test_jpeg(self):
        # JPEG magic bytes: FF D8 FF
        data = b'\xff\xd8\xff\xe0' + b'\x00' * 100
        result = adapter.MaxAdapter._detect_image_mime(data)
        assert result == "image/jpeg"

    def test_webp(self):
        # WebP: RIFF....WEBP
        data = b'RIFF\x00\x00\x00\x00WEBP' + b'\x00' * 100
        result = adapter.MaxAdapter._detect_image_mime(data)
        assert result == "image/webp"

    def test_gif(self):
        # GIF: GIF8
        data = b'GIF89a' + b'\x00' * 100
        result = adapter.MaxAdapter._detect_image_mime(data)
        assert result == "image/gif"

    def test_bmp(self):
        # BMP: BM
        data = b'BM' + b'\x00' * 100
        result = adapter.MaxAdapter._detect_image_mime(data)
        assert result == "image/bmp"

    def test_unknown_fallback(self):
        data = b'\x00' * 100
        result = adapter.MaxAdapter._detect_image_mime(data)
        assert result == "image/jpeg"

    def test_short_data(self):
        data = b'\x89PNG'  # Less than 12 bytes
        result = adapter.MaxAdapter._detect_image_mime(data)
        assert result == "image/jpeg"


class TestStreamingThrottle:
    """Tests for edit_message streaming throttle."""

    def _make_adapter(self):
        from gateway.config import PlatformConfig
        cfg = PlatformConfig(enabled=True, token="test-token", extra={"token": "test-token"})
        a = adapter.MaxAdapter(cfg)
        a._http_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        a._http_client.put = AsyncMock(return_value=mock_resp)
        a.send_typing = AsyncMock()
        return a

    @pytest.mark.asyncio
    async def test_first_edit_goes_through(self):
        a = self._make_adapter()
        result = await a.edit_message("user:42", "mid-1", "hello world")
        assert result.success is True
        a._http_client.put.assert_called_once()
        # Typing should be renewed
        a.send_typing.assert_called_once_with("user:42")

    @pytest.mark.asyncio
    async def test_rapid_edits_throttled(self):
        a = self._make_adapter()

        # First edit — goes through
        result1 = await a.edit_message("user:42", "mid-1", "first")
        assert result1.success is True

        # Second edit immediately — throttled
        result2 = await a.edit_message("user:42", "mid-1", "second")
        assert result2.success is True
        # Should only have one actual HTTP call (second was throttled)
        assert a._http_client.put.call_count == 1

    @pytest.mark.asyncio
    async def test_finalize_resets_throttle(self):
        a = self._make_adapter()

        # First edit
        await a.edit_message("user:42", "mid-1", "first")
        assert a._http_client.put.call_count == 1

        # Finalize edit — always goes through, resets throttle
        result = await a.edit_message("user:42", "mid-1", "final", finalize=True)
        assert result.success is True
        assert a._http_client.put.call_count == 2
        assert getattr(a, "_last_edit_at", 0.0) == 0.0


class TestTypingRenewal:
    """Tests for typing indicator renewal after edit."""

    def _make_adapter(self):
        from gateway.config import PlatformConfig
        cfg = PlatformConfig(enabled=True, token="test-token", extra={"token": "test-token"})
        a = adapter.MaxAdapter(cfg)
        a._http_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        a._http_client.put = AsyncMock(return_value=mock_resp)
        a.send_typing = AsyncMock()
        return a

    @pytest.mark.asyncio
    async def test_typing_renewed_after_edit(self):
        a = self._make_adapter()
        await a.edit_message("user:42", "mid-1", "streaming...")
        # send_typing must be called after successful edit
        a.send_typing.assert_called_once_with("user:42")

    @pytest.mark.asyncio
    async def test_typing_not_renewed_on_error(self):
        a = self._make_adapter()
        a._http_client.put.side_effect = Exception("API error")
        await a.edit_message("user:42", "mid-1", "fail")
        # send_typing should NOT be called on error
        a.send_typing.assert_not_called()
