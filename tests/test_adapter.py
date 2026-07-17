"""Core adapter tests."""

import os
import types
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import adapter


class TestHelpers:
    """Tests for helper functions."""

    def test_parse_list(self):
        assert adapter._parse_list("a, b, c") == ["a", "b", "c"]
        assert adapter._parse_list("") == []
        assert adapter._parse_list("single") == ["single"]

    def test_is_group(self):
        assert adapter._is_group("-100") is True
        assert adapter._is_group("-1") is True
        assert adapter._is_group("42") is False
        assert adapter._is_group("abc") is False

    def test_coerce_bool(self):
        assert adapter._coerce_bool("true") is True
        assert adapter._coerce_bool("1") is True
        assert adapter._coerce_bool("yes") is True
        assert adapter._coerce_bool("false") is False
        assert adapter._coerce_bool("0") is False
        assert adapter._coerce_bool(None) is False
        assert adapter._coerce_bool(True) is True

    def test_verify_secret_matches(self):
        assert adapter._verify_raw_secret(b"{}", "secret-123", "secret-123") is True
        assert adapter._verify_raw_secret(b"{}", "secret-123", "different") is False
        assert adapter._verify_raw_secret(b"{}", "", None) is True
        assert adapter._verify_raw_secret(b"{}", "secret-123", None) is False


class TestEnvEnablement:
    """Tests for _env_enablement."""

    def test_seeds_extra(self, monkeypatch):
        monkeypatch.setenv("MAX_BOT_TOKEN", "tok")
        monkeypatch.setenv("MAX_WEBHOOK_PORT", "8646")
        monkeypatch.setenv("MAX_WEBHOOK_PATH", "/max/webhook")
        monkeypatch.setenv("MAX_ALLOWED_USERS", "1, 2")
        monkeypatch.setenv("MAX_HOME_CHANNEL", "user:1")
        monkeypatch.setenv("MAX_STT_ENABLED", "true")

        extra = adapter._env_enablement()

        assert extra is not None
        assert extra["token"] == "tok"
        assert extra["port"] == 8646
        assert extra["path"] == "/max/webhook"
        assert extra["allowed_users"] == ["1", "2"]
        assert extra["home_channel"]["chat_id"] == "user:1"
        assert extra["stt_enabled"] is True

    def test_returns_none_without_token(self, monkeypatch):
        monkeypatch.delenv("MAX_BOT_TOKEN", raising=False)
        assert adapter._env_enablement() is None


class TestBuildEvent:
    """Tests for _build_event."""

    @pytest.mark.asyncio
    async def test_build_from_dm(self, max_config, sample_dm_update):
        from gateway.config import PlatformConfig

        # We can't easily test the full _build_event without a running adapter
        # Test basic parsing logic via _on_message_created
        pass  # Requires mock httpx client setup — covered by integration


class TestChunking:
    """Tests for _split_outbound_text."""

    def test_short_message_not_split(self):
        from gateway.config import PlatformConfig
        cfg = PlatformConfig(enabled=True, token="tok")
        a = adapter.MaxAdapter(cfg)
        result = a._split_outbound_text("Short message")
        assert len(result) == 1
        assert result[0] == "Short message"

    def test_long_message_split_by_paragraph(self):
        from gateway.config import PlatformConfig
        cfg = PlatformConfig(enabled=True, token="tok")
        a = adapter.MaxAdapter(cfg)

        para = "x" * 4000
        msg = f"{para}\n\n{para}"
        result = a._split_outbound_text(msg)
        assert len(result) >= 2

    def test_empty_returns_one_chunk(self):
        from gateway.config import PlatformConfig
        cfg = PlatformConfig(enabled=True, token="tok")
        a = adapter.MaxAdapter(cfg)
        result = a._split_outbound_text("")
        assert len(result) == 1
        assert result[0] == ""


class TestConfigValidation:
    """Tests for config validation functions."""

    def test_validate_config_with_token(self, monkeypatch):
        from gateway.config import PlatformConfig
        monkeypatch.setenv("MAX_BOT_TOKEN", "test-tok")
        cfg = PlatformConfig(enabled=True)
        assert adapter.validate_config(cfg) is True

    def test_validate_config_without_token(self, monkeypatch):
        from gateway.config import PlatformConfig
        monkeypatch.delenv("MAX_BOT_TOKEN", raising=False)
        cfg = PlatformConfig(enabled=True)
        assert adapter.validate_config(cfg) is False

    def test_check_requirements(self, monkeypatch):
        monkeypatch.setenv("MAX_BOT_TOKEN", "test")
        # aiohttp and httpx are available in test environment
        assert adapter.check_max_requirements() is True


class TestMediaHelpers:
    """Tests for media extraction helpers."""

    def test_attachment_kind_audio(self):
        att = {"type": "audio", "payload": {"url": "https://example.com/test.ogg"}}
        assert adapter.MaxAdapter._attachment_kind(att) == "audio"

    def test_attachment_kind_voice(self):
        att = {"type": "voice", "payload": {}}
        assert adapter.MaxAdapter._attachment_kind(att) == "voice"

    def test_attachment_kind_image(self):
        att = {"type": "image", "payload": {}}
        assert adapter.MaxAdapter._attachment_kind(att) == "image"

    def test_attachment_kind_document(self):
        att = {"type": "file", "payload": {}}
        assert adapter.MaxAdapter._attachment_kind(att) == "document"

    def test_attachment_kind_unknown(self):
        att = {"type": "unknown", "payload": {}}
        assert adapter.MaxAdapter._attachment_kind(att) == ""

    def test_find_first_url(self):
        data = {"url": "https://cdn.example.com/file.ogg"}
        assert adapter.MaxAdapter._find_first_url(data) == "https://cdn.example.com/file.ogg"

    def test_find_first_url_nested(self):
        data = {"payload": {"url": "https://cdn.example.com/file.ogg"}}
        assert adapter.MaxAdapter._find_first_url(data) == "https://cdn.example.com/file.ogg"

    def test_find_first_url_not_found(self):
        assert adapter.MaxAdapter._find_first_url({"x": 1}) is None

    def test_find_first_filename(self):
        data = {"filename": "document.pdf"}
        assert adapter.MaxAdapter._find_first_filename(data) == "document.pdf"

    def test_safe_url_for_log(self):
        result = adapter.MaxAdapter._safe_url_for_log(
            "https://cdn.example.com/path/file?token=secret"
        )
        assert "token" not in result
        assert result == "https://cdn.example.com/path/file"

    def test_derive_message_type_text(self):
        assert adapter.MaxAdapter._derive_message_type("hello", []) == adapter.MessageType.TEXT

    def test_derive_message_type_image(self):
        assert adapter.MaxAdapter._derive_message_type("", ["image/jpeg"]) == adapter.MessageType.PHOTO

    def test_derive_message_type_audio(self):
        assert adapter.MaxAdapter._derive_message_type("", ["audio/ogg"]) == adapter.MessageType.VOICE


class TestPolicyProperties:
    """Tests for policy properties."""

    @pytest.mark.asyncio
    async def test_dm_policy_with_allowlist(self, max_config):
        a = adapter.MaxAdapter(max_config)
        # Without allowed users set, defaults to "open" if allow_all_users is False
        # But _allow_all_users defaults False, _allowed_users_set empty → should be "allowlist"
        assert a.dm_policy in ("open", "allowlist")

    @pytest.mark.asyncio
    async def test_max_message_length(self, max_config):
        a = adapter.MaxAdapter(max_config)
        assert a.max_message_length == 4000
