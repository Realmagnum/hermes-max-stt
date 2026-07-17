# Hermes MAX STT — Platform Plugin

**Proper Hermes Agent plugin** for MAX messenger (max.ru) with built-in voice transcription.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Hermes](https://img.shields.io/badge/Hermes-Agent-8A2BE2)](https://hermes-agent.nousresearch.com/docs)

## Features

| Feature | Description |
|---------|-------------|
| 🟣 **Max Messenger** | Full gateway integration with max.ru |
| 📡 **Dual Mode** | Long polling (`GET /updates`) + Webhook (`POST /max/webhook`) |
| 🎤 **STT Voice** | Auto-download voice messages → local path → faster-whisper transcription |
| 📝 **Streaming** | `edit_message` via `PUT /messages` for live token streaming |
| ✂️ **Chunking** | Smart 4000-char message splitting preserving paragraphs |
| 🖼️ **Tables as Images** | Render markdown tables as Pillow-generated PNGs with colored status icons |
| 🔒 **Access Control** | Per-user allowlist, group policies, webhook secret verification |
| 📎 **Media** | Recursive attachment extraction, image/document/audio caching |
| ⬆️ **Upload** | Two-step file upload (`POST /uploads` → PUT file → token) |
| ⚡ **Typing** | Typing indicator for all chat types |
| 🧪 **Tested** | pytest + pytest-asyncio test suite |
| 🔧 **Interactive Setup** | `hermes gateway setup` with prompts |

## Comparison

| | Upstream (vladimiraldushin) | This plugin |
|---|---|---|
| Architecture | Plugin ✅ | Plugin ✅ |
| Long Polling | ❌ Webhook only | ✅ Both modes |
| STT Voice | ❌ | ✅ Built-in |
| Streaming edit | ❌ | ✅ edit_message |
| Message dedup | ❌ | ✅ 300s window |
| File upload | ❌ | ✅ Two-step |
| Message chunking | ✅ | ✅ Improved |
| Media extraction | ✅ | ✅ Extended |
| Tests | ✅ Basic | ✅ Extended |
| Interactive setup | ✅ | ✅ + STT option |

## Quick Start

### 1. Install

```bash
hermes plugins install Realmagnum/hermes-max-stt --enable
```

### 2. Get a Max bot token

1. Register at https://business.max.ru/self (requires Russian юрлицо/ИП/самозанятый)
2. Create a bot → pass moderation
3. Copy token from **Чат-боты → Перейти → Расширенные настройки → Настроить**

### 3. Configure

```bash
hermes gateway setup
# Choose: Max (STT)
# Follow the interactive prompts
```

Or manually in `~/.hermes/.env`:

```bash
MAX_BOT_TOKEN=your_token_here
MAX_ALLOWED_USERS=your_max_user_id
MAX_STT_ENABLED=true
```

### 4. Restart

```bash
hermes gateway restart
```

### 5. Optional: STT (voice transcription)

```bash
python3 -m venv ~/.hermes/stt-venv
~/.hermes/stt-venv/bin/pip install faster-whisper

# Copy the transcription script
cp scripts/transcribe_audio.py ~/.hermes/scripts/
```

For HTTPS webhook (production), expose port 8646 via Cloudflare Tunnel or Traefik.

### 6. Optional: Tables as Images

Render markdown pipe-tables as clean PNG images with colored status icons instead of monospace text.

```bash
# Install Pillow (required)
pip install Pillow

# Enable in .env
echo 'MAX_TABLE_AS_IMAGE=true' >> ~/.hermes/.env

# Restart gateway
hermes gateway restart
```

When enabled, the adapter renders tables like:

```
`-------------------------`
`| Server  | Status      |`
`|---------|-------------|`
`| web-01  | ✓ Done      |`   →  colored PNG with icons
`| db-main | ✗ Failed    |`
`-------------------------`
```

Supports ✅✓ ✗ ❌ ⚠ ⏳ emoji → colored Unicode symbols (✓ ✗ ⚠ ◷ ▶ ●) with green/red/orange/amber/blue text.

| Symbol | Meaning | Color |
|--------|---------|-------|
| ✓ Done | Green `#16a34a` |
| ✗ Failed | Red `#dc2626` |
| ⚠ In review/warning | Orange `#ea580c` |
| ◷ Pending | Amber `#ca8a04` |
| ▶ Scheduled | Blue `#3b82f6` |

Falls back to text rendering if Pillow is not installed.

## Configuration Reference

| Env Variable | Required | Default | Description |
|-------------|----------|---------|-------------|
| `MAX_BOT_TOKEN` | ✅ | — | Bot token from Max Platform |
| `MAX_WEBHOOK_HOST` | ❌ | `0.0.0.0` | Webhook bind host |
| `MAX_WEBHOOK_PORT` | ❌ | `8646` | Webhook bind port |
| `MAX_WEBHOOK_PATH` | ❌ | `/max/webhook` | Webhook URL path |
| `MAX_WEBHOOK_SECRET` | ❌ | — | Secret for X-Max-Bot-Api-Secret |
| `MAX_WEBHOOK_URL` | ❌ | — | Public HTTPS URL (enables webhook mode) |
| `MAX_ALLOWED_USERS` | ❌ | — | Comma-separated user IDs |
| `MAX_ALLOW_ALL_USERS` | ❌ | `false` | Allow all users |
| `MAX_STT_ENABLED` | ❌ | `true` | Auto-download voice for STT |
| `MAX_TABLE_AS_IMAGE` | ❌ | `false` | Render tables as Pillow-generated PNG images |
| `MAX_HOME_CHANNEL` | ❌ | — | Default cron/send_message target |

## Troubleshooting

### Bot not responding

1. Check gateway status: `hermes gateway status`
2. Check Max /me: `curl -H "Authorization: $MAX_BOT_TOKEN" https://platform-api.max.ru/me`
3. Verify webhook: `curl http://localhost:8646/health`

### SSL errors with Max API

Max uses Russian MinCifry CA. For testing:
```bash
MAX_INSECURE_SSL=true
```

### Voice not transcribing

1. Check `MAX_STT_ENABLED=true` in `.env`
2. Verify venv: `~/.hermes/stt-venv/bin/pip list | grep faster-whisper`
3. Test manually: `python3 scripts/transcribe_audio.py --latest`

## Project Structure

```
hermes-max-stt/
├── plugin.yaml              # Hermes plugin metadata
├── __init__.py              # register() entry point
├── pyproject.toml           # Python package config
├── adapter.py               # MaxAdapter (~2600 lines)
├── scripts/
│   └── transcribe_audio.py  # STT transcription
├── skills/
│   └── max-gateway/
│       └── SKILL.md         # Agent skill
├── tests/                   # pytest test suite
├── AGENTS.md                # Instructions for AI agents
├── after-install.md         # Post-install guide
└── .github/workflows/ci.yml # CI/CD
```

## Security

This plugin follows secure-by-default practices:

| Measure | Detail |
|---------|--------|
| 🛡️ **SSRF Protection** | File upload URLs validated against `*.max.ru` / `*.oneme.ru` whitelist |
| 🔐 **Token Safety** | `Authorization` header never forwarded on HTTP redirects |
| 🔑 **Webhook Secret** | Constant-time comparison via `secrets.compare_digest` |
| 🔊 **Voice Privacy** | Audio cache stored with `0700` permissions |
| 🧹 **Error Sanitization** | Token/URLs stripped from error messages returned to gateway |
| 🔍 **CI Hardening** | `bandit` SAST + `pip-audit` dependency scanning on every push |

Full audit and fixes: commit `e87ee64`.

## License

MIT — see [LICENSE](LICENSE)

## Credits

- Based on [vladimiraldushin/hermes-max-platform](https://github.com/vladimiraldushin/hermes-max-platform) — original plugin architecture
- [Hermes Agent](https://hermes-agent.nousresearch.com/docs) — the agent framework
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — voice transcription
