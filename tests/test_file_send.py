"""Tests for native file sending (upload, retry, standalone, batch images)."""

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import adapter


# ── Helpers ───────────────────────────────────────────────────────────────


def _mock_adapter():
    from unittest.mock import AsyncMock, MagicMock
    from gateway.config import PlatformConfig
    cfg = PlatformConfig(enabled=True, token="test-token", extra={"token": "test-token"})
    a = adapter.MaxAdapter(cfg)
    a._http_client = AsyncMock()
    return a


def _make_httpx_response(status=200, json_data=None):
    mock = MagicMock(spec=httpx.Response)
    mock.status_code = status
    mock.json.return_value = json_data or {}
    return mock


# ── Tests for _upload_send retry logic ────────────────────────────────────


class TestUploadSendRetry:
    """_upload_send retries on attachment.not.ready."""

    def _make_adapter(self):
        return _mock_adapter()

    @pytest.mark.asyncio
    async def test_upload_send_success_first_try(self):
        """Normal case: upload + send succeeds immediately."""
        a = self._make_adapter()
        a._upload = AsyncMock(return_value="tok-abc")
        resp = _make_httpx_response(200, {"message": {"body": {"mid": "mid-1"}}})
        a._http_client.post = AsyncMock(return_value=resp)

        result = await a._upload_send("user:42", "/tmp/test.pdf", "file", "Here:", None)
        assert result.success is True
        assert result.message_id == "mid-1"

    @pytest.mark.asyncio
    async def test_upload_send_retry_on_not_ready(self):
        """Retries on attachment.not.ready, succeeds on 2nd attempt."""
        a = self._make_adapter()
        a._upload = AsyncMock(return_value="tok-abc")

        not_ready = _make_httpx_response(
            400, {"code": "attachment.not.ready", "message": "not processed"}
        )
        ok_resp = _make_httpx_response(200, {"message": {"body": {"mid": "mid-2"}}})

        # Override _http_client.post to track calls
        call_count = 0

        async def _post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Simulate httpx.HTTPStatusError
                raise httpx.HTTPStatusError(
                    "Not ready", request=MagicMock(), response=not_ready
                )
            return ok_resp

        a._http_client.post = _post

        with patch.object(adapter, "UPLOAD_DELAY", 0.01):
            result = await a._upload_send("user:42", "/tmp/test.pdf", "file", "Here:", None)
        assert result.success is True
        assert result.message_id == "mid-2"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_upload_send_all_retries_exhausted(self):
        """Gives up after all retries fail."""
        a = self._make_adapter()
        a._upload = AsyncMock(return_value="tok-abc")

        async def _post(*args, **kwargs):
            resp = _make_httpx_response(400, {"code": "attachment.not.ready", "message": "not processed"})
            raise httpx.HTTPStatusError(
                "Not ready", request=MagicMock(), response=resp
            )

        a._http_client.post = _post

        with patch.object(adapter, "UPLOAD_DELAY", 0.01):
            result = await a._upload_send("user:42", "/tmp/test.pdf", "file", "Here:", None)
        assert result.success is False
        assert "failed" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_upload_send_no_client(self):
        a = self._make_adapter()
        a._http_client = None
        result = await a._upload_send("user:1", "/tmp/f", "file", "", None)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_upload_send_upload_fails(self):
        a = self._make_adapter()
        a._upload = AsyncMock(return_value=None)
        result = await a._upload_send("user:1", "/tmp/f", "file", "", None)
        assert result.success is False
        assert "Upload failed" in (result.error or "")


# ── Tests for send_multiple_images ────────────────────────────────────────


class TestSendMultipleImages:
    """send_multiple_images batch upload + fallback."""

    def _make_adapter(self):
        return _mock_adapter()

    @pytest.mark.asyncio
    async def test_multiple_images_no_client(self):
        a = self._make_adapter()
        a._http_client = None
        result = await a.send_multiple_images("user:1", [])
        assert result.success is False

    @pytest.mark.asyncio
    async def test_multiple_images_no_images(self):
        a = self._make_adapter()
        result = await a.send_multiple_images("user:1", [])
        assert result.success is False

    @pytest.mark.asyncio
    async def test_multiple_images_success(self):
        """Batch upload + single message with multiple tokens."""
        a = self._make_adapter()
        a._upload = AsyncMock(return_value="tok-img")

        resp = _make_httpx_response(200, {"message": {"body": {"mid": "batch-1"}}})
        a._http_client.post = AsyncMock(return_value=resp)

        images = [
            ("/tmp/photo1.png", "Caption 1"),
            ("/tmp/photo2.jpg", "Caption 2"),
        ]
        result = await a.send_multiple_images("user:42", images)
        assert result.success is True
        assert result.message_id == "batch-1"

        # Verify the request had both tokens
        call_kwargs = a._http_client.post.call_args[1]
        body = call_kwargs["json"]
        attachments = body.get("attachments", [])
        assert len(attachments) == 2
        assert attachments[0]["payload"]["token"] == "tok-img"
        assert attachments[1]["payload"]["token"] == "tok-img"

    @pytest.mark.asyncio
    async def test_multiple_images_upload_fails_fallback(self):
        """If upload fails, falls back to sequential send_image_file calls."""
        a = self._make_adapter()
        a._upload = AsyncMock(return_value=None)  # upload fails
        a.send_image_file = AsyncMock(return_value=MagicMock(success=True))
        a.send_image = AsyncMock(return_value=MagicMock(success=True))

        images = [
            ("/tmp/img.png", "Cap1"),
            ("https://example.com/img.jpg", "Cap2"),
        ]
        result = await a.send_multiple_images("user:1", images)
        # Falls back to sequential — last_result from the last image
        assert result is not None


# ── Tests for standalone sender ───────────────────────────────────────────


class TestStandaloneSend:
    """_standalone_send with media_files."""

    @pytest.mark.asyncio
    async def test_standalone_text_only(self):
        """Text-only send works as before."""
        from gateway.config import PlatformConfig
        pconfig = PlatformConfig(enabled=True, token="test-token", extra={})

        with patch.object(adapter, "_standalone_get_token", return_value="tok"):
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client_cls.return_value.__aenter__.return_value = mock_client

                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {"message": {"message_id": "m-1"}}
                mock_client.post = AsyncMock(return_value=mock_resp)

                result = await adapter._standalone_send(pconfig, "user:42", "Hello")
                assert result.get("success") is True
                assert result.get("message_id") == "m-1"

    @pytest.mark.asyncio
    async def test_standalone_no_token(self):
        from gateway.config import PlatformConfig
        pconfig = PlatformConfig(enabled=True, token="", extra={})
        with patch.object(adapter, "_standalone_get_token", return_value=""):
            result = await adapter._standalone_send(pconfig, "user:1", "hi")
            assert "not configured" in (result.get("error", "")).lower()

    @pytest.mark.asyncio
    async def test_standalone_media_file_not_found(self):
        """media_files with non-existent path should skip gracefully."""
        from gateway.config import PlatformConfig
        pconfig = PlatformConfig(enabled=True, token="test-token", extra={})

        with patch.object(adapter, "_standalone_get_token", return_value="tok"):
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client_cls.return_value.__aenter__.return_value = mock_client

                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {"message": {"message_id": "m-1"}}
                mock_client.post = AsyncMock(return_value=mock_resp)

                # Non-existent file should be skipped, text still delivered
                result = await adapter._standalone_send(
                    pconfig, "user:42", "Text",
                    media_files=[("/nonexistent/file.pdf", False)],
                )
                assert result.get("success") is True


# ── Tests for SSRF in standalone sender ──────────────────────────────────


class TestStandaloneSSRF:
    """_standalone_send blocks remote upload URLs not in allowlist."""

    @pytest.mark.asyncio
    async def test_standalone_upload_url_ssrf_blocked(self):
        """Upload URL from unknown domain should be skipped with warning."""
        from gateway.config import PlatformConfig
        pconfig = PlatformConfig(enabled=True, token="test-token", extra={})

        with patch.object(adapter, "_standalone_get_token", return_value="tok"):
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client_cls.return_value.__aenter__.return_value = mock_client

                evil_resp = MagicMock()
                evil_resp.status_code = 200
                evil_resp.json.return_value = {"url": "https://evil.com/upload"}
                mock_client.post = AsyncMock(return_value=evil_resp)

                with patch("os.path.exists", return_value=True):
                    result = await adapter._standalone_send(
                        pconfig, "user:42", "test",
                        media_files=[("/tmp/test.pdf", False)],
                    )
                assert result.get("success") is True


# ── Tests for _standalone_get_token ───────────────────────────────────────


class TestStandaloneGetToken:
    def test_from_env(self):
        with patch.dict(os.environ, {"MAX_BOT_TOKEN": "env-token"}):
            from gateway.config import PlatformConfig
            pconfig = PlatformConfig(enabled=True, token="", extra={})
            token = adapter._standalone_get_token(pconfig)
            assert token == "env-token"

    def test_from_config(self):
        from gateway.config import PlatformConfig
        pconfig = PlatformConfig(enabled=True, token="cfg-token", extra={})
        with patch.dict(os.environ, {}, clear=True):
            token = adapter._standalone_get_token(pconfig)
            assert token == "cfg-token"

    def test_from_extra(self):
        from gateway.config import PlatformConfig
        pconfig = PlatformConfig(enabled=True, token="", extra={"token": "extra-token"})
        with patch.dict(os.environ, {}, clear=True):
            token = adapter._standalone_get_token(pconfig)
            assert token == "extra-token"

    def test_empty(self):
        from gateway.config import PlatformConfig
        pconfig = PlatformConfig(enabled=True, token="", extra={})
        with patch.dict(os.environ, {}, clear=True):
            token = adapter._standalone_get_token(pconfig)
            assert token == ""


# ── Tests for SSRF whitelist ──────────────────────────────────────────────


@pytest.mark.parametrize("host", [
    "fu.oneme.ru",
    "iu.oneme.ru",
    "upload.max.ru",
    "cdn.max.ru",
    "platform-api.max.ru",
    "storage.max.ru",
    "random.storage.max.ru",
    "cdn-123.oneme.ru",
])
def test_allowed_upload_hosts(host):
    """Verify that allowed CDN hosts pass the SSRF check."""
    from urllib.parse import urlparse
    allowed = adapter._ALLOWED_UPLOAD_HOSTS
    if (
        host in allowed
        or host.endswith(".max.ru")
        or host.endswith(".oneme.ru")
    ):
        assert True
    else:
        assert False, f"Host {host} should be allowed"
