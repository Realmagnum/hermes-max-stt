# Max STT plugin installed

Next steps:

1. **Install runtime dependencies:**
   ```bash
   pip install aiohttp httpx
   # For STT voice transcription:
   pip install faster-whisper
   ```

2. **Configure the platform:**
   ```bash
   hermes gateway setup
   ```
   Choose **Max (STT)**, paste `MAX_BOT_TOKEN`, set webhook host/port/path and optional secret.

3. **Set up voice transcription (optional):**
   ```bash
   python3 -m venv ~/.hermes/stt-venv
   ~/.hermes/stt-venv/bin/pip install faster-whisper
   cp scripts/transcribe_audio.py ~/.hermes/scripts/
   ```

4. **Choose connection mode:**

   **Long polling (simpler, no HTTPS):**
   - Just set `MAX_BOT_TOKEN` and restart. The adapter auto-uses long-polling.
   - No public URL needed. Good for development.

   **Webhook (production):**
   - Expose the local webhook server as public HTTPS:
     ```bash
     cloudflared tunnel --url http://localhost:8646
     # or: ngrok http 8646
     ```
   - Register the public URL with Max:
     ```bash
     curl -X POST "https://platform-api.max.ru/subscriptions" \
       -H "Authorization: $MAX_BOT_TOKEN" \
       -H "Content-Type: application/json" \
       -d '{"url":"https://YOUR-DOMAIN/max/webhook","update_types":["message_created","message_callback","bot_started"],"secret":"CHANGE_ME_5_256_CHARS"}'
     ```

5. **Restart Hermes gateway:**
   ```bash
   hermes gateway restart
   ```

6. **Verify:**
   ```bash
   hermes gateway status
   curl http://localhost:8646/health
   # Expected: {"status":"ok"}
   ```

## Official Max docs

Checked on 2026-06-22:
- https://dev.max.ru/docs/chatbots/bots-create
- https://dev.max.ru/docs/chatbots/bots-coding/prepare
- https://dev.max.ru/docs-api/methods/POST/subscriptions
- https://dev.max.ru/docs-api/methods/POST/messages
