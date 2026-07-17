# Changelog

All notable changes to the hermes-max-stt plugin.

## [2.1.3] — 2026-07-17

### Security (audit finalization)

- **MEDIUM:** Sanitized 5 remaining `error=str(e)` returns in: `edit_message`, `send_image`, `_upload_send`, `_post_interactive`, `_standalone_send` (token/URL leak prevention in outbound methods)
- **MEDIUM:** Added per-IP rate limiting to webhook handler — 30 req/10s window, auto-cleanup at 1000+ entries
- **MEDIUM:** Hard 5000-entry cap on `_seen_msgs` dedup dictionary (memory exhaustion prevention under DDoS)
- **MEDIUM:** Strict validation of clarify callback `choice_idx`: `isdigit()` guard + bounds check (0–256)
- **LOW:** Removed unused `import hmac` (superseded by `secrets.compare_digest`)

## [2.1.2] — 2026-07-17

### Added

- **Tables as Images** (`MAX_TABLE_AS_IMAGE=true`): render pipe-markdown tables as Pillow-generated PNG images with colored status icons:
  - ✓ Done (green), ✗ Failed (red), ⚠ In review (orange), ◷ Pending (amber), ▶ Scheduled (blue)
  - Emoji variation selector normalization (U+FE0F/U+FE0E stripping)
  - Dark header (#1e293b) with white text, alternating row colors
  - Auto-fallback to text rendering when Pillow is missing
  - Image upload via two-step API (`POST /uploads` → PUT → token → send)

### Fixed

- **Markdown table rendering:** removed unsupported fenced code blocks (```) and `<pre>` tags — MAX supports neither. Tables now render as inline `code` spans for monospace text, or as PNG images when `MAX_TABLE_AS_IMAGE=true`
- **Upload domain whitelist:** added `iu.oneme.ru` and `*.oneme.ru` to SSRF allowlist (MAX image CDN)
- **Emoji rendering in table images:** replaced emoji with proper Unicode symbols that DejaVu Sans renders clearly (16×15px vs 8×8px blobs)

## [2.1.1] — 2026-07-17

### Security (audit + hardening)

- **CRITICAL:** Fixed command injection vector in STT subprocess — path now escaped via `shlex.quote()`
- **CRITICAL:** Added SSRF protection — file upload URLs validated against `*.max.ru` domain whitelist
- **HIGH:** Disabled `follow_redirects` on authenticated HTTP client (token leak prevention)
- **HIGH:** Removed `follow_redirects=True` from attachment download methods
- **HIGH:** Sanitized error messages returned to gateway (no raw exception strings with URLs)
- **HIGH:** Renamed `_verify_secret` → `_verify_raw_secret`, uses `secrets.compare_digest` for clarity
- **MEDIUM:** Removed `os.environ` mutation in `_apply_yaml_config` (side-effect risk)
- **LOW:** Audio cache created with `0700` permissions (voice message privacy)
- **LOW:** Health endpoint no longer discloses platform name
- **LOW:** `transcribe_audio.py`: `shlex.quote(model_name)` for defense-in-depth

### CI

- Added `bandit` SAST scan job
- Added `pip-audit` dependency vulnerability scan job

## [2.1.0] — 2026-07-15

### Added

- Initial release: MAX messenger platform adapter with STT voice transcription
- Dual mode: long polling + webhook
- Recursive media extraction and caching
- Two-step file upload
- Message streaming via `edit_message`
- Smart 4000-char message chunking
- Inline keyboard buttons (approval, clarify, model picker)
- Group access control policies
- Interactive `hermes gateway setup` flow
- Standalone sender for cron/send_message
- faster-whisper STT integration
