---
name: max-gateway
description: "Install and configure Hermes Agent access through Max messenger with STT voice transcription."
version: 2.0.0
author: Alexander / Hermes Agent community
license: MIT
metadata:
  hermes:
    tags: [hermes, gateway, messaging, max, chatbot, stt, voice, whisper]
---

# Max Gateway for Hermes (with STT)

Use this skill when a user wants to control Hermes Agent through Max messenger.

## Official facts to trust first

Checked on 2026-06-22:
- Max partner platform connection: https://dev.max.ru/docs/maxbusiness/connection
- Chatbot creation and token location: https://dev.max.ru/docs/chatbots/bots-create
- Developer setup and token warning: https://dev.max.ru/docs/chatbots/bots-coding/prepare
- API overview: https://dev.max.ru/docs-api
- Webhook subscriptions: https://dev.max.ru/docs-api/methods/POST/subscriptions
- Sending messages: https://dev.max.ru/docs-api/methods/POST/messages

If these docs changed, follow the current official docs instead of this skill.

## Procedure

1. Verify Hermes is installed: `hermes --version`
2. Install plugin dependencies:
   ```bash
   pip install aiohttp httpx
   pip install faster-whisper  # for STT voice transcription
   ```
3. Install and enable the plugin:
   ```bash
   hermes plugins install Realmagnum/hermes-max-integration --enable
   ```
   Or from local path:
   ```bash
   hermes plugins install /path/to/hermes-max-integration-plugin --enable
   ```
4. Help the user get a Max bot token.
   Official path after moderation:
   `Chat-bots → Go → Advanced settings → Configure → Token`
5. Save token as `MAX_BOT_TOKEN` in Hermes `.env`. Do not echo the token back.
6. Configure webhook bind settings (default):
   ```
   MAX_WEBHOOK_HOST=0.0.0.0
   MAX_WEBHOOK_PORT=8646
   MAX_WEBHOOK_PATH=/max/webhook
   ```
7. Create a public HTTPS tunnel to `http://localhost:8646` or use the user's HTTPS domain.
8. Register subscription:
   ```bash
   curl -X POST "https://platform-api.max.ru/subscriptions" \
     -H "Authorization: $MAX_BOT_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"url":"https://YOUR-DOMAIN/max/webhook","update_types":["message_created","message_callback","bot_started"],"secret":"CHANGE_ME_5_256_CHARS"}'
   ```
9. Restart and verify:
   ```bash
   hermes gateway restart
   hermes gateway status
   curl http://localhost:8646/health
   ```
10. Ask the user to send a real message to the Max bot and verify Hermes answers.

## Voice Messages (STT)

When the agent receives a message with `[Audio: /path/to/file.ogg]`:

1. The adapter auto-downloads voice messages to `~/.hermes/audio_cache/`
2. Transcribe with:
   ```bash
   python3 scripts/transcribe_audio.py /path/to/audio.ogg
   ```
3. Or for most recent:
   ```bash
   python3 scripts/transcribe_audio.py --latest
   ```

### Models

| Model | Speed (CPU) | Accuracy | Use case |
|-------|------------|----------|----------|
| `tiny` | ~0.1s | ★★☆ | Quick test |
| `base` | ~2-3s | ★★★ | Daily use (default) |
| `small` | ~5-8s | ★★★★ | Important messages |

Options: `--model tiny|base|small`, `--language ru|auto`

### Pitfalls for STT

- Uses venv at `~/.hermes/stt-venv/` (faster-whisper)
- `base` model: good accuracy, ~2-3s on CPU. Still mishears complex words.
- `tiny` model: fast (0.1s), less accurate — expect garbled words
- Audio files cleaned after 7 days
- Script timeout: 120s

## Pitfalls (general)

- Use `Authorization: *** not query params and not `Bearer <token>`.
- Webhook must be HTTPS with a trusted certificate.
- If `secret` is configured, Max sends it raw in `X-Max-Bot-Api-Secret`; compare it directly with constant-time comparison.
- **🚨 CRITICAL: Webhook and Long Polling are mutually exclusive.** If a webhook subscription exists in MAX API, `/updates` returns empty and ALL messages go to the webhook URL instead. Even after removing `MAX_WEBHOOK_URL` from .env and restarting, the stale subscription persists in MAX API and silently blocks message delivery.
  - **Fix:** Delete the old subscription:
    ```bash
    curl -X DELETE "https://platform-api.max.ru/subscriptions?url=<URL>" -H "Authorization: $MAX_BOT_TOKEN"
    ```
  - **Auto-fix (v2.1.4+):** The plugin now auto-cleans stale webhook subscriptions on startup when running in long-polling mode.
  - **Prevention:** Don't set `MAX_WEBHOOK_URL` in .env unless you have a working reverse proxy in front of port 8646. When switching modes, always clean up the old subscription first.
- Keep tunnel/gateway running while using Max.
- Max API requires Russian Federation jurisdiction for bot registration.
