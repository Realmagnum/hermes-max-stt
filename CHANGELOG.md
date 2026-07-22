# Список изменений

Все заметные изменения в плагине hermes-max-integration.

## [2.3.0] — 2026-07-22

### Added

- **Bilingual documentation policy** — all `*.md` files now follow RU-primary + EN-translation (`*_EN.md`) pattern:
  - `AGENTS.md` + `AGENTS_EN.md`
  - `CHANGELOG.md` + `CHANGELOG_EN.md`
  - `README.md` + `README_EN.md`
  - `docs/webhook.md` + `docs/webhook_EN.md`
  - `after-install.md` + `after-install_EN.md`
  - `skills/max-gateway/SKILL.md` + `skills/max-gateway/SKILL_EN.md`

### Fixed

- `send_buttons()` now wraps one button per row (full width) instead of 2 per row
- Button text auto-truncated to MAX API limits (40 chars callback, 64 chars link)
- `send_buttons()` — double-encoded JSON bug fix in `send_typing` (mentioned in v2.2.0)

### Changed

- `send_buttons()` — text duplicated in message body as fallback (mobile readability)
- `send_buttons()` — auto-numbering (`1.`, `2.`, `3.`...) when 3+ buttons
- `send_buttons()` — optional `label` field: full description in message body, short `text` on button

## [2.2.0] — 2026-07-22

### Добавлено

- **Кросс-платформенные команды сессий** — `/sessions` теперь показывает сессии со ВСЕХ платформ (💻CLI, 📱Telegram, 🟣MAX, 🎮Discord, 🌐WebUI, 🔌API Server), не только MAX:
  - Богатый вывод с эмодзи платформы, превью заголовка и сокращённым ID сессии
  - `/sessions search <query>` — полнотекстовый поиск по всем платформам
  - `/resume <id>` автоматически использует флаг `--all` — возобновление любой сессии с любой платформы
  - Настраивается через `MAX_CROSS_SESSION=true|false` (по умолчанию: true)
  - Требует `allow_admin_from` в config.yaml для платформы `max` (добавляет MAX ID пользователя), чтобы работал core-флаг `--all`
- **`send_action()`** — расширенные действия чата: `typing`, `typing_off`, `sending_photo`, `sending_video`, `sending_audio`, `sending_file`, `read`. Заменяет старый `send_typing()`, который теперь делегирует выполнение `send_action()`.
- **`send_buttons()`** — публичный метод для отправки сообщений с inline-кнопками любых типов: `callback`, `link`, `message`, `request_contact`, `request_geo_location`.
  - Одна кнопка на ряд (полная ширина)
  - Текст кнопок автоматически обрезается до лимитов MAX API (40 символов для callback, 64 для link)
  - Авто-нумерация (`1.`, `2.`, `3.`...) при 3+ кнопках
  - Опциональное поле `label`: полный текст описания в теле сообщения (никогда не обрезается)
  - Запасной текст с содержимым кнопок дублируется в теле сообщения
- **`plugin.yaml`** — добавлена опциональная env-переменная `MAX_CROSS_SESSION`

### Исправлено

- `send_typing` содержал баг двойной сериализации JSON (`json.dumps` оборачивал словарь, который затем снова сериализовал `httpx`) — исправлено передачей словаря напрямую в `json=`.

### Примечания

- Когда `MAX_CROSS_SESSION=false`, `/sessions` и `/resume` возвращаются к стандартному поведению ядра (только сессии MAX)
- Кросс-платформенное возобновление (`/resume --all`) требует, чтобы MAX ID пользователя был указан в `platforms.max.extra.allow_admin_from` в config.yaml

## [2.1.3] — 2026-07-17

### Безопасность (завершение аудита)

- **MEDIUM:** Очищены 5 оставшихся `error=str(e)` в: `edit_message`, `send_image`, `_upload_send`, `_post_interactive`, `_standalone_send` (предотвращение утечки токенов/URL в исходящих методах)
- **MEDIUM:** Добавлено per-IP ограничение скорости для webhook-обработчика — 30 запросов/10с, автоочистка при 1000+ записей
- **MEDIUM:** Жёсткий лимит в 5000 записей для словаря дедубликации `_seen_msgs` (предотвращение истощения памяти при DDoS)
- **MEDIUM:** Строгая валидация `choice_idx` в clarify-колбэке: проверка `isdigit()` + проверка границ (0–256)
- **LOW:** Удалён неиспользуемый `import hmac` (заменён на `secrets.compare_digest`)

## [2.1.2] — 2026-07-17

### Добавлено

- **Таблицы как изображения** (`MAX_TABLE_AS_IMAGE=true`): рендеринг markdown-таблиц как PNG-изображений с цветными иконками статусов:
  - ✓ Готово (зелёный), ✗ Ошибка (красный), ⚠ На проверке (оранжевый), ◷ Ожидание (янтарный), ▶ Запланировано (синий)
  - Нормализация вариативных селекторов эмодзи (U+FE0F/U+FE0E)
  - Тёмный заголовок (#1e293b) с белым текстом, чередующиеся цвета строк
  - Автоматическое запасное текстовое отображение при отсутствии Pillow
  - Загрузка изображений через двухшаговое API (`POST /uploads` → PUT → токен → отправка)

### Исправлено

- **Рендеринг Markdown-таблиц:** удалены неподдерживаемые блоки кода (```) и теги `<pre>` — MAX не поддерживает ни то, ни другое. Таблицы теперь отображаются как inline-блоки `code` для моноширинного текста или как PNG-изображения при `MAX_TABLE_AS_IMAGE=true`
- **Белый список доменов загрузки:** добавлены `iu.oneme.ru` и `*.oneme.ru` в SSRF-разрешительный список (CDN изображений MAX)
- **Рендеринг эмодзи в таблицах:** эмодзи заменены на соответствующие Unicode-символы, которые DejaVu Sans отображает чётко (16×15px вместо 8×8px)

## [2.1.1] — 2026-07-17

### Безопасность (аудит + усиление защиты)

- **CRITICAL:** Исправлен вектор внедрения команд в STT-подпроцессе — путь теперь экранируется через `shlex.quote()`
- **CRITICAL:** Добавлена защита от SSRF — URL загружаемых файлов проверяются по белому списку доменов `*.max.ru`
- **HIGH:** Отключено `follow_redirects` в аутентифицированном HTTP-клиенте (предотвращение утечки токена)
- **HIGH:** Удалён `follow_redirects=True` из методов загрузки вложений
- **HIGH:** Очищены сообщения об ошибках, возвращаемые шлюзу (без сырых строк исключений с URL)
- **HIGH:** Переименован `_verify_secret` → `_verify_raw_secret`, использует `secrets.compare_digest` для ясности
- **MEDIUM:** Удалена мутация `os.environ` в `_apply_yaml_config` (риск побочных эффектов)
- **LOW:** Кэш аудио создаётся с правами `0700` (приватность голосовых сообщений)
- **LOW:** Health-эндпоинт больше не раскрывает имя платформы
- **LOW:** `transcribe_audio.py`: `shlex.quote(model_name)` для защиты в глубину

### CI

- Добавлен SAST-сканер `bandit`
- Добавлен сканер уязвимостей зависимостей `pip-audit`

## [2.1.0] — 2026-07-15

### Добавлено

- Первый релиз: адаптер платформы MAX messenger с STT-транскрипцией голоса
- Два режима: long polling + webhook
- Рекурсивное извлечение и кэширование медиа
- Двухшаговая загрузка файлов
- Стриминг сообщений через `edit_message`
- Умное чанкование сообщений по 4000 символов
- Inline-клавиатуры (approval, clarify, выбор модели)
- Политики контроля доступа для групп
- Интерактивный `hermes gateway setup`
- Самостоятельный отправитель для cron/send_message
- Интеграция faster-whisper STT
