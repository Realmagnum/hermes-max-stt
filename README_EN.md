# Hermes MAX Gateway

> **⚠️ Bilingual Project:** The primary documentation language is **Russian**. English translation is in `README_EN.md`. When modifying this file, **always** sync changes with `README.md`.

**Hermes Agent gateway plugin for MAX messenger (max.ru).**  
Voice transcription (STT), interactive buttons (model picker, approval, clarify), table-as-image rendering (PNG with colored icons), streaming responses, file upload, access control.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Hermes](https://img.shields.io/badge/Hermes-Agent-8A2BE2)](https://hermes-agent.nousresearch.com/docs)

---

## Features

| Feature | Description |
|---------|-------------|
| 🟣 **Max Messenger** | Full gateway integration with max.ru |
| 📡 **Dual Mode** | Long polling (`GET /updates`) + Webhook (`POST /max/webhook`) |
| 🎤 **STT Voice** | Auto-download voice messages → faster-whisper transcription |
| 🖼️ **Tables as Images** | Render markdown tables as Pillow-generated PNGs with colored status icons |
| 📝 **Streaming** | `edit_message` via `PUT /messages` for live token streaming |
| 🔘 **Interactive Buttons** | Model picker (`/model`), exec approval, slash confirm, clarify |
| ✂️ **Auto-chunking** | Smart 4000-char message splitting preserving paragraphs |
| ⬆️ **File Upload** | Two-step upload: `POST /uploads` → PUT → token → send |
| 🔒 **Access Control** | Per-user allowlist, group policies, webhook secret verification |
| 📎 **Media** | Recursive attachment extraction, image/document/audio caching |
| 🎞️ **Voice/Video/Docs** | Dedicated `send_voice`, `send_video`, `send_document` methods |
| ⚡ **Typing Indicator** | Shows "user is typing" for all chat types |
| 🔧 **Standalone Sender** | Cron/send_message via `_standalone_send` with native file delivery. `hermes send "text MEDIA:/file"` works without core mod |
| 🧪 **Tested** | pytest + pytest-asyncio, **126 tests** |
| 🔧 **Interactive Setup** | `hermes gateway setup` with prompts |

## Tables as Images in Action

**Without** `MAX_TABLE_AS_IMAGE` (text fallback):
```
`-------------------------`
`| Server   | Status     |`
`| web-01   | ✓ Done     |`
`| db-main  | ✗ Failed   |`
`-------------------------`
```

**With** `MAX_TABLE_AS_IMAGE=true` (PNG image, ~13KB):

![Example table image](assets/table_sample.png)

Each status cell gets a colored icon: ✓ green, ✗ red, ⚠ orange, ◷ amber, ▶ blue.

### Use Cases

| Scenario | Before | After |
|----------|--------|-------|
| 📊 **Monitoring dashboard** | Raw pipe text | Clean table with status icons |
| 📋 **Task list** | Hard to read | Clear columns with priorities |
| 🏗️ **CI/CD pipeline** | Broken layout | PNG with stages ready to share |
| 📈 **Reports** | Collapsed columns | Well-formatted table image |
| 👥 **Team projects** | Visual noise | Color-coded progress table |

### Why images instead of native tables?

**Telegram** supports markdown tables natively — just send `| A | B |` with `format=markdown`, and the client renders columns, borders, and alignment automatically.

**MAX** does not support tables in markdown. The supported formatting is limited to `*italic*`, `**bold**`, `` `code` ``, `[links](url)`, `# headings`, `> quotes`. Pipe syntax (`| A | B |`) and fenced code blocks (`` ``` ``) are not in the supported list.

We tried several approaches before settling on PNG:

| Attempt | Result |
|---------|--------|
| `` ``` `` fenced code block | MAX doesn't support it — fences rendered as literal text |
| `<pre>` HTML tag | Only works in HTML mode, which breaks markdown in the rest of the message |
| inline `` `code` `` | Works as a fallback, but no borders or alignment |
| Plain text with `\|` and `---` | Readable but looks messy without monospace |
| **Pillow PNG** ✅ | **Full control: colors, borders, icons, fonts** |

**Bottom line:** PNG images deliver what Telegram provides natively — clean tables with colored status badges. Bonus: images can be forwarded and don't depend on the client's markdown parser. Trade-off: cell text is not copyable.

## Comparison with Upstream

| | Upstream (vladimiraldushin) | This plugin |
|---|---|---|
| Architecture | Plugin ✅ | Plugin ✅ |
| Long Polling | ❌ Webhook only | ✅ Both modes |
| STT Voice | ❌ | ✅ Built-in |
| Streaming (edit_message) | ❌ | ✅ |
| **Tables as Images (PNG)** | ❌ | ✅ **Unique** |
| **Interactive Buttons** | ❌ | ✅ model picker, approval, clarify |
| File upload | ❌ | ✅ Two-step |
| Message chunking | ✅ | ✅ Improved |
| Media extraction | ✅ | ✅ Extended |
| Message dedup | ❌ | ✅ 300s window |
| Tests | ✅ Basic | ✅ 94 tests |
| Interactive setup | ✅ | ✅ + STT + tables |

## Architecture

```
┌─────────┐     Long Polling / Webhook     ┌─────────────────┐
│  MAX    │ ──────────────────────────────→ │  MaxAdapter     │
│  Client │                                  │  (adapter.py)   │
│  (bot)  │ ←────────────────────────────── │     ↓           │
└─────────┘     POST /messages (text/PNG)  │  ┌───────────┐  │
                                            │  │ send()    │  │
                                            │  │  ↓        │  │
                                            │  │ tables?   │──┼── MAX_TABLE_AS_IMAGE=true
                                            │  │  ↓   ↓    │  │    → Pillow → PNG
                                            │  │ text PNG  │  │    → POST /uploads
                                            │  │       │   │  │    → PUT → token
                                            │  └───────────┘  │    → POST /messages
                                            │  ┌───────────┐  │
                                            │  │ STT (opt) │──┼── faster-whisper
                                            │  └───────────┘  │
                                            └─────────────────┘
```

## Quick Start

### 1. Install

```bash
hermes plugins install Realmagnum/hermes-max-integration --enable
```

### 2. Get a bot token

Register at https://business.max.ru/self (requires Russian legal entity / sole proprietor).
Create a bot → pass moderation → **Чат-боты → Перейти → Расширенные настройки → Настроить** → copy token.

### 3. Configure

```bash
hermes gateway setup
# Choose: Max (STT)
```

Or manually in `~/.hermes/.env`:

```bash
MAX_BOT_TOKEN=your_token_here
MAX_ALLOWED_USERS=your_max_user_id
```

### 4. Enable table images (optional)

```bash
pip install Pillow
echo 'MAX_TABLE_AS_IMAGE=true' >> ~/.hermes/.env
```

### 5. Restart

```bash
hermes gateway restart
```

## Connection Modes

The plugin supports two modes for receiving messages from MAX API. The mode is determined by a single variable — **`MAX_WEBHOOK_URL`**:

| `MAX_WEBHOOK_URL` | Mode | Mechanism |
|---|---|---|
| Not set (empty) | **Long polling** (default) | Cyclic `GET /updates?timeout=5&marker=...` |
| HTTPS URL set | **Webhook** | aiohttp server on port 8646, `POST /subscriptions` registration |

The choice happens in `connect()` with one line: `self._use_webhook = bool(self._webhook_url)`.

### Switching Long polling → Webhook

```bash
# 1. Add to ~/.hermes/.env
MAX_WEBHOOK_URL=https://your-domain.com/max/webhook
MAX_WEBHOOK_SECRET=my-secret

# 2. Restart
sudo systemctl restart hermes-gateway
```

On startup: `_start_webhook()` → opens `0.0.0.0:8646` → registers a subscription in MAX API → messages arrive via webhook.

### Switching Webhook → Long polling

```bash
# 1. Remove or comment out MAX_WEBHOOK_URL (and MAX_WEBHOOK_SECRET)
# MAX_WEBHOOK_URL=...
# MAX_WEBHOOK_SECRET=...

# 2. Restart
sudo systemctl restart hermes-gateway
```

On startup: `_start_polling()` → checks `GET /subscriptions`, **automatically removes** stale webhook subscriptions (otherwise MAX would keep sending to a dead URL) → starts `_poll_loop`.

### 🚨 Important

If a webhook subscription was registered **manually** (via curl, not through the plugin), auto-cleanup may not find it. In that case, delete it manually:

```bash
curl -X DELETE "https://platform-api.max.ru/subscriptions?url=<URL>" \
  -H "Authorization: $MAX_BOT_TOKEN"
```

Check active subscriptions: `GET /subscriptions` with the same token.

### 💬 Reasoning display (model thinking)

When using reasoning models (DeepSeek R1, Claude Opus, Gemini Thinking, etc.), the reasoning block (`💭 **Reasoning:**`) is automatically prepended to the final response.

To ensure reasoning appears as a **fresh separate message** (rather than an edit of the last streamed draft), add to `~/.hermes/config.yaml`:

```yaml
display:
  platforms:
    max:
      fresh_final_after_seconds: 10
```

This tells the gateway to deliver the final answer as a new message if streaming lasted longer than 10 seconds — the reasoning block is included in full. Without this setting, reasoning is prepended to the last streaming edit and may go unnoticed.

## Configuration Reference

| Env Variable | Required | Default | Description |
|-------------|----------|---------|-------------|
| `MAX_BOT_TOKEN` | ✅ | — | Bot token from Max Platform |
| `MAX_API_BASE` | ❌ | `https://platform-api.max.ru` | API base URL (docs now recommend `https://platform-api2.max.ru`) |
| `MAX_WEBHOOK_HOST` | ❌ | `0.0.0.0` | Webhook bind host |
| `MAX_WEBHOOK_PORT` | ❌ | `8646` | Webhook bind port |
| `MAX_WEBHOOK_PATH` | ❌ | `/max/webhook` | Webhook URL path |
| `MAX_WEBHOOK_SECRET` | ❌ | — | Secret for X-Max-Bot-Api-Secret |
| `MAX_WEBHOOK_URL` | ❌ | — | Public HTTPS URL (enables webhook mode) |
| `MAX_ALLOWED_USERS` | ❌ | — | Comma-separated user IDs |
| `MAX_ALLOW_ALL_USERS` | ❌ | `false` | Allow all users |
| `MAX_GROUP_ALLOWED_USERS` | ❌ | — | User IDs allowed to interact in group chats |
| `MAX_GROUP_ALLOWED_CHATS` | ❌ | — | Chat group IDs where bot is allowed |
| `MAX_STT_ENABLED` | ❌ | `true` | Auto-download voice for STT |
| `MAX_STT_VENV` | ❌ | `~/.hermes/stt-venv` | Path to faster-whisper venv |
| `MAX_TABLE_AS_IMAGE` | ❌ | `false` | Render tables as Pillow-generated PNG images |
| `MAX_HOME_CHANNEL` | ❌ | — | Default cron/send_message target |
| `MAX_HOME_CHANNEL_NAME` | ❌ | — | Default channel name |
| `MAX_INSECURE_SSL` | ❌ | `false` | Disable SSL verification (testing only) |

## Table Image Symbol Reference

| Input Emoji | Rendered As | Meaning | Color |
|------------|-------------|---------|-------|
| ✅ | ✓ | Done | `#16a34a` |
| ❌ | ✗ | Failed / Error | `#dc2626` |
| ⚠️ | ⚠ | In review / Warning | `#ea580c` |
| ⏳ / ⌛ | ◷ | Pending | `#ca8a04` |
| ⏳ + "scheduled" | ▶ | Scheduled | `#3b82f6` |
| 🔴 | ● | Critical (red) | `#dc2626` |
| 🟢 | ● | Good (green) | `#16a34a` |
| 🟡 | ● | Mid (yellow) | `#ca8a04` |

Auto-fallbacks to inline `` `code` `` text if Pillow is not installed.

---

## 📎 Native File Delivery (standalone sender)

The plugin can send files via `hermes send` without a running gateway:

```bash
# Text + file (works without core modification)
hermes send --to max:USER_ID "📄 Report MEDIA:/path/to/report.pdf"

# Multiple files
hermes send --to max:USER_ID "📦 Files: MEDIA:/tmp/a.pdf MEDIA:/tmp/b.xlsx"

# MEDIA-only (requires optional core patch — see below)
```

**How it works:**

1. Core extracts `MEDIA:` paths → `media_files: List[Tuple[str, bool]]`
2. Text is sent as a separate `POST /messages`
3. For each file:
    - `POST /uploads?type=file` → CDN upload URL
    - Multipart POST to CDN → file token
    - `POST /messages` with `attachments: [{"type": "file", "payload": {"token": token}}]`

All files use `type=file` — MAX CDN does not validate content, guaranteeing delivery for any safe extension (.txt, .md, .png, .jpg, .mp3, .pdf, .doc, .xlsx, etc.).

⚠️ **MAX CDN limitation:** Extensions `.exe`, `.apk`, `.bat`, `.msi` and other potentially dangerous types are rejected by MAX CDN (HTTP 415 — "File extension is forbidden"). This is a platform limitation, not addressable from the plugin.

For **in-session** file delivery (via gateway), use `send_image_file()`, `send_document()`, `send_voice()`, `send_video()` — these use the adapter with retry on `attachment.not.ready`.

### Optional: MEDIA-only core support

By default `hermes send "MEDIA:/file"` (no text) is blocked by core:

```
send_message MEDIA delivery is currently only supported for telegram, discord...
```

Fix with the optional script that adds MAX to the supported platform list in `tools/send_message_tool.py`:

```bash
python3 scripts/apply-core-fix.py       # apply
python3 scripts/apply-core-fix.py --revert  # revert
```

After applying:
```bash
hermes send --to max:USER_ID "MEDIA:/tmp/image.png"      # ✅ works
hermes send --to max:USER_ID "text MEDIA:/file.pdf"       # ✅ already worked
```

## Troubleshooting

### Bot not responding

```bash
hermes gateway status
curl -H "Authorization: ***" https://platform-api.max.ru/me
curl http://localhost:8646/health
```

### Tables not rendering as images

```bash
# Check config
grep MAX_TABLE_AS_IMAGE ~/.hermes/.env

# Check Pillow
pip list | grep Pillow

# Check logs
grep -i "table\|upload\|pillow" ~/.hermes/logs/gateway.log
```

### SSL errors with Max API

Max uses Russian MinCifry CA certificates. For testing: `MAX_INSECURE_SSL=true`

### Voice not transcribing

```bash
grep MAX_STT_ENABLED ~/.hermes/.env
~/.hermes/stt-venv/bin/pip list | grep faster-whisper
python3 scripts/transcribe_audio.py --latest
```

## Project Structure

```
hermes-max-integration/
├── plugin.yaml              # Hermes plugin metadata
├── __init__.py              # register() entry point
├── pyproject.toml           # Python package config
├── adapter.py               # MaxAdapter (~2600 lines)
├── scripts/
│   ├── apply-core-fix.py      # Optional core patch for MEDIA-only
│   └── transcribe_audio.py  # STT transcription
├── skills/
│   └── max-gateway/
│       └── SKILL.md         # Agent skill
├── tests/                   # pytest: 126 tests
├── AGENTS.md                # Instructions for AI agents
├── after-install.md         # Post-install guide
├── cliff.toml               # git-cliff config (EN)
├── cliff-ru.toml            # git-cliff config (RU)
├── README.md                # Russian version
├── docs/
│   └── webhook.md           # Webhook architecture (Russian)
└── .github/workflows/ci.yml # CI/CD
```

**Note:** generated table PNGs are cached in `~/.hermes/table_images/`.

## Security

| Measure | Detail |
|---------|--------|
| 🛡️ **SSRF Protection** | Upload URLs validated against `*.max.ru` / `*.oneme.ru` whitelist |
| 🔐 **Token Safety** | `Authorization` header never forwarded on HTTP redirects |
| 🔑 **Webhook Secret** | Constant-time comparison via `secrets.compare_digest` |
| 🔊 **Voice Privacy** | Audio cache stored with `0700` permissions |
| 🧹 **Error Sanitization** | Tokens/URLs stripped from error messages |
| 🔍 **CI Hardening** | `bandit` SAST + `pip-audit` on every push |

Full audit and fixes: commit `e87ee64`.

## Project History

The project evolved in two stages.

**The first version** was written from scratch for a specific goal: bridging Hermes Agent with the MAX messenger. It introduced voice transcription, two-step file uploads, interactive buttons, and response streaming — features that no other MAX plugin had at the time.

**Later**, a more mature project — [vladimiraldushin/hermes-max-platform](https://github.com/vladimiraldushin/hermes-max-platform) — came to our attention, with well-thought-out plugin architecture, webhooks, and tests. Rather than maintaining two parallel branches, we decided to rework the plugin on top of this foundation:

- Architecture, subscriptions (webhook/long polling), update system — from upstream
- All features from the first version (STT, table images, buttons, streaming, file uploads) — ported and extended
- On top of that, capabilities found in neither original branch: PNG table rendering, improved model picker, standalone cron sender, group policies

**The result** is a hybrid: a solid upstream foundation combined with unique functionality found nowhere else.

## License

MIT — see [LICENSE](LICENSE)

## Credits

- [vladimiraldushin/hermes-max-platform](https://github.com/vladimiraldushin/hermes-max-platform) — architecture base for v2.0 (subscriptions, webhooks, plugin structure)
- Original v1.0 development — Realmagnum (STT, table images, buttons, streaming, file upload)
- [Hermes Agent](https://hermes-agent.nousresearch.com/docs) — the agent framework
