# Instructions for AI agents using this repository

This repository contains a Hermes Agent platform plugin that adds Max messenger as a gateway channel with STT voice transcription.

When a user gives you this repository and asks to connect Hermes to Max:

1. **Do not invent** Max bot setup steps. Use the fact-checked sources in `README.md`; if available, re-open the official pages under `https://dev.max.ru/` and prefer current official docs.
2. Install Hermes first if it is missing: https://hermes-agent.nousresearch.com/docs
3. Install this plugin with:
   ```bash
   hermes plugins install Realmagnum/hermes-max-stt --enable
   ```
4. Ensure `aiohttp` and `httpx` are installed in the same Python environment that runs Hermes.
5. Help the user obtain `MAX_BOT_TOKEN` from Max for Partners.
   Current checked path: `Chat-bots → Go → Advanced settings → Configure → Token` after bot moderation.
6. Treat `MAX_BOT_TOKEN` and `MAX_WEBHOOK_SECRET` as secrets. Do not print them back to the chat.
7. Configure a public HTTPS webhook URL that points to the local Hermes gateway server, default local URL `http://localhost:8646/max/webhook`.
   Or use long-polling mode (no HTTPS needed) — just set the token and restart.
8. For webhook mode, register through Max Bot API:
   ```bash
   curl -X POST "https://platform-api.max.ru/subscriptions" \
     -H "Authorization: <token>" \
     -H "Content-Type: application/json" \
     -d '{"url":"https://your-domain/max/webhook","update_types":["message_created","message_callback","bot_started"],"secret":"your-secret"}'
   ```
9. Restart Hermes gateway and verify:
   ```bash
   hermes gateway restart
   hermes gateway status
   curl http://localhost:8646/health
   ```
10. For STT (voice transcription), install faster-whisper:
    ```bash
    python3 -m venv ~/.hermes/stt-venv
    ~/.hermes/stt-venv/bin/pip install faster-whisper
    cp scripts/transcribe_audio.py ~/.hermes/scripts/
    ```
11. When the agent receives a voice message (marked as `[Audio: /path/to/file.ogg]`), transcribe it:
    ```bash
    ~/.hermes/scripts/transcribe_audio.py /path/to/file.ogg
    ```

**Important current Max API facts** (checked 2026-07-17):
- Bot API requests use `Authorization: *** header; token in query parameters is no longer supported.
- Webhook requires public HTTPS; HTTP and self-signed certificates are not supported for webhooks.
- Webhook `secret` is sent back by Max as the raw `X-Max-Bot-Api-Secret` header value, not as an HMAC signature.
- For production, Max recommends Webhook, not Long Polling; both cannot be used simultaneously.
- `POST /messages` accepts `user_id` or `chat_id`; message `text` is up to 4000 characters and `format` can be `markdown` or `html`.
- This plugin supports **both** long-polling (default, no HTTPS needed) and webhook (requires HTTPS). Long-polling is ideal for development and testing.
- **Callback updates (`message_callback`)** contain a `message` object; the chat_id for routing lives at `message.recipient.chat_id`, NOT at `chat.chat_id` or top-level `chat_id`.

**STT-specific:**
- Voice messages from Max come as audio attachments with `payload.url` for direct download.
- The adapter auto-downloads them to `~/.hermes/audio_cache/max_audio_{message_id}.ogg` when `MAX_STT_ENABLED=true`.
- Use the `scripts/transcribe_audio.py` script with faster-whisper for transcription.
- Default model: `base` (best speed/accuracy trade-off on CPU).
