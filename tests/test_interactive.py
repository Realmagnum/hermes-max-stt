"""Tests for interactive buttons (approval, slash-confirm, clarify, send_action, send_buttons)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import adapter


class TestPostInteractive:
    """Tests for _post_interactive."""

    def _make_adapter(self):
        from gateway.config import PlatformConfig
        cfg = PlatformConfig(enabled=True, token="test-token", extra={"token": "test-token"})
        a = adapter.MaxAdapter(cfg)
        a._http_client = AsyncMock()
        return a

    @pytest.mark.asyncio
    async def test_post_interactive_dm(self):
        a = self._make_adapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": {"body": {"mid": "mid-123"}}}
        a._http_client.post = AsyncMock(return_value=mock_resp)

        buttons = [[
            {"type": "callback", "text": "Btn", "payload": "btn:1"},
        ]]
        result = await a._post_interactive("user:42", "Test message", buttons)
        assert result.success is True
        assert result.message_id == "mid-123"
        a._http_client.post.assert_called_once_with(
            f"{adapter.MAX_API_BASE}/messages",
            params={"user_id": "42"},
            json={
                "text": "Test message",
                "format": "markdown",
                "attachments": [{
                    "type": "inline_keyboard",
                    "payload": {"buttons": buttons},
                }],
            },
        )

    @pytest.mark.asyncio
    async def test_post_interactive_no_client(self):
        a = self._make_adapter()
        a._http_client = None
        buttons = [[{"type": "callback", "text": "X", "payload": "x"}]]
        result = await a._post_interactive("user:42", "Test", [])
        assert result.success is False


class TestSendAction:
    """Tests for send_action (extended chat actions)."""

    def _make_adapter(self):
        from gateway.config import PlatformConfig
        cfg = PlatformConfig(enabled=True, token="test-token", extra={"token": "test-token"})
        a = adapter.MaxAdapter(cfg)
        a._http_client = AsyncMock()
        return a

    @pytest.mark.asyncio
    async def test_send_typing_delegates_to_send_action(self):
        a = self._make_adapter()
        a.send_action = AsyncMock()
        await a.send_typing("user:42")
        a.send_action.assert_called_once_with("user:42", "typing")

    @pytest.mark.asyncio
    async def test_send_action_typing(self):
        a = self._make_adapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        a._http_client.post = AsyncMock(return_value=mock_resp)
        await a.send_action("user:42", "typing")
        a._http_client.post.assert_called_once()
        call_body = a._http_client.post.call_args[1]["json"]
        assert call_body["action"] == "typing_on"

    @pytest.mark.asyncio
    async def test_send_action_all_types(self):
        a = self._make_adapter()
        expected = {
            "typing": "typing_on", "typing_on": "typing_on",
            "typing_off": "typing_off", "sending_photo": "sending_photo",
            "sending_video": "sending_video", "sending_audio": "sending_audio",
            "sending_file": "sending_file", "read": "read",
        }
        for action, expected_api in expected.items():
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            a._http_client.post = AsyncMock(return_value=mock_resp)
            await a.send_action("chat:123", action)
            body = a._http_client.post.call_args[1]["json"]
            assert body["action"] == expected_api, f"{action} → {body['action']}"

    @pytest.mark.asyncio
    async def test_send_action_chat_id_scoping(self):
        a = self._make_adapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        a._http_client.post = AsyncMock(return_value=mock_resp)
        await a.send_action("chat:123", "typing")
        url = a._http_client.post.call_args[0][0]
        assert "chats/123/actions" in url

    @pytest.mark.asyncio
    async def test_send_action_no_client(self):
        a = self._make_adapter()
        a._http_client = None
        await a.send_action("user:1", "typing")
        await a.send_action("user:1", "sending_file")


class TestSendButtons:
    """Tests for send_buttons (generic inline buttons)."""

    def _make_adapter(self):
        from gateway.config import PlatformConfig
        cfg = PlatformConfig(enabled=True, token="test-token", extra={"token": "test-token"})
        a = adapter.MaxAdapter(cfg)
        a._http_client = AsyncMock()
        return a

    @pytest.mark.asyncio
    async def test_send_buttons_link_type(self):
        a = self._make_adapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": {"body": {"mid": "mid-link"}}}
        a._http_client.post = AsyncMock(return_value=mock_resp)
        result = await a.send_buttons(
            chat_id="user:42", text="Links:",
            buttons=[
                {"type": "link", "text": "GitHub", "url": "https://github.com"},
                {"type": "link", "text": "Docs", "url": "https://hermes-agent.ai"},
            ],
        )
        assert result.success is True
        body = a._http_client.post.call_args[1]["json"]
        attach = body["attachments"][0]
        assert attach["type"] == "inline_keyboard"
        rows = attach["payload"]["buttons"]
        # One button per row
        assert len(rows) == 2
        assert rows[0] == [{"type": "link", "text": "GitHub", "url": "https://github.com"}]
        assert rows[1] == [{"type": "link", "text": "Docs", "url": "https://hermes-agent.ai"}]

    @pytest.mark.asyncio
    async def test_send_buttons_mixed_types(self):
        a = self._make_adapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": {"body": {"mid": "m"}}}
        a._http_client.post = AsyncMock(return_value=mock_resp)
        result = await a.send_buttons(
            chat_id="chat:99", text="Actions:",
            buttons=[
                {"type": "link", "text": "Site", "url": "https://x.com"},
                {"type": "callback", "text": "Ok", "payload": "ok"},
                {"type": "request_contact", "text": "Contact"},
            ],
        )
        assert result.success is True
        rows = a._http_client.post.call_args[1]["json"]["attachments"][0]["payload"]["buttons"]
        # 3 buttons → 3 rows, one button each
        assert len(rows) == 3
        assert all(len(row) == 1 for row in rows)

    @pytest.mark.asyncio
    async def test_send_buttons_max_10(self):
        a = self._make_adapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": {"body": {"mid": "m"}}}
        a._http_client.post = AsyncMock(return_value=mock_resp)
        many = [{"type": "callback", "text": f"B{i}", "payload": str(i)} for i in range(15)]
        result = await a.send_buttons("user:1", "Max:", many)
        assert result.success is True
        rows = a._http_client.post.call_args[1]["json"]["attachments"][0]["payload"]["buttons"]
        assert sum(len(r) for r in rows) == 10

    @pytest.mark.asyncio
    async def test_send_buttons_no_client(self):
        a = self._make_adapter()
        a._http_client = None
        result = await a.send_buttons("user:1", "text", [])
        assert result.success is False
