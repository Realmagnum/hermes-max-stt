# Hermes MAX Gateway

> **⚠️ Двуязычный проект:** Основной язык документации — **русский**. Английский перевод — `README_EN.md`. При изменении этого файла **обязательно** синхронизируйте изменения с `README_EN.md`.

**Плагин-шлюз для подключения Hermes Agent к мессенджеру MAX.**  
Голосовая транскрипция (STT), интерактивные кнопки (выбор модели, подтверждение команд), отрисовка таблиц в PNG-картинки с цветными иконками, стриминг ответов, загрузка файлов, контроль доступа.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Hermes](https://img.shields.io/badge/Hermes-Agent-8A2BE2)](https://hermes-agent.nousresearch.com/docs)

---

## Возможности

| Функция | Описание |
|---------|----------|
| 🟣 **MAX Messenger** | Полная интеграция шлюза с max.ru |
| 📡 **Два режима** | Long polling (`GET /updates`) + Webhook (`POST /max/webhook`) |
| 🎤 **STT Голос** | Автозагрузка голосовых сообщений → faster-whisper транскрипция |
| 🖼️ **Таблицы-картинки** | Отрисовка markdown-таблиц в PNG с цветными иконками статусов |
| 📝 **Стриминг** | `edit_message` через `PUT /messages` для вывода токенов в реальном времени |
| 🔘 **Интерактивные кнопки** | callback + link + message + request_contact/geo + модель/approval/clarify |
| 🔗 **Link-кнопки** | Кнопки-ссылки в сообщениях (`send_buttons()` с типом `link`) |
| 👁️ **send_action** | Расширенные статусы: typing, sending_photo/video/audio/file, read, typing_off |
| ✂️ **Авточанкование** | Умная разбивка длинных сообщений (до 4000 символов с сохранением абзацев) |
| ⬆️ **Загрузка файлов** | Двухшаговая загрузка: `POST /uploads` → PUT → токен → отправка |
| 🔒 **Контроль доступа** | Белый список пользователей, групповые политики, проверка секрета вебхука |
| 📎 **Медиа** | Рекурсивное извлечение вложений, кэш изображений/документов/аудио |
| 🎞️ **Голосовые/Видео/Документы** | Отдельные методы `send_voice`, `send_video`, `send_document` |
| ⚡ **Индикатор ввода** | Отображение набора текста для всех типов чатов |
| 🔧 **Standalone-отправитель** | Отправка сообщений из cron/send_message через `_standalone_send` с нативной доставкой файлов. `hermes send "текст MEDIA:/file"` — работает без модификации ядра |
| 🌐 **Кросс-платформенные сессии** | `/sessions` показывает сессии со ВСЕХ платформ, `/resume <id>` переключается на любую. Включено по умолчанию (`MAX_CROSS_SESSION=true`) |
| 🧪 **Тесты** | pytest + pytest-asyncio, **126 тестов** |
| 🔧 **Интерактивная настройка** | `hermes gateway setup` с подсказками |

## Пример: таблицы-картинки в деле

**Без** `MAX_TABLE_AS_IMAGE` (текстовый fallback):
```
`-------------------------`
`| Сервер   | Статус     |`
`| web-01   | ✓ Done     |`
`| db-main  | ✗ Failed   |`
`-------------------------`
```

**С** `MAX_TABLE_AS_IMAGE=true` (PNG-картинка, ~13KB):

![Пример таблицы-картинки](assets/table_sample.png)

Каждая ячейка статуса — цветной символ: ✓ зелёный, ✗ красный, ⚠ оранжевый, ◷ янтарный, ▶ синий.

### Где это полезно

| Сценарий | Что было раньше | Что стало |
|----------|----------------|-----------|
| 📊 **Дашборд мониторинга** | «| Сервер | Статус |» текстом | Цветная таблица с иконками |
| 📋 **Список задач** | Нечитаемые строки | Чёткие колонки с приоритетами |
| 🏗️ **CI/CD статус** | Слитые строки | Аккуратный PNG с этапами |
| 📈 **Отчёты** | Развалившаяся разметка | Готовая для пересылки картинка |
| 👥 **Командные проекты** | Путаница в колонках | Понятная таблица с цветами |

### Почему картинка, а не нативная таблица?

**Telegram** поддерживает markdown-таблицы «из коробки» — достаточно отправить `| A | B |` с `format=markdown`, и клиент сам отрисует колонки, границы, выравнивание.

**MAX** не поддерживает таблицы в markdown. Из доступных вариантов форматирования есть только `*курсив*`, `**жирный**`, `` `код` ``, `[ссылки](url)`, `# заголовки`, `> цитаты`. Pipe-синтаксис (`| A | B |`) и fenced code blocks (`` ``` ``) не входят в список поддерживаемых.

Мы перепробовали несколько подходов, прежде чем остановились на PNG:

| Попытка | Результат |
|---------|-----------|
| ` ``` ` code fence | MAX не поддерживает — теги отображались как текст |
| `<pre>` HTML-тег | Работает только в HTML-режиме, но тогда весь остальной markdown перестаёт парситься |
| inline `` `code` `` | Работает как fallback, но без границ и выравнивания |
| Простой текст с `\|` и `---` | Читаемо, но без моноширинного шрифта выглядит неаккуратно |
| **Pillow PNG** ✅ | **Полный контроль: цвета, границы, иконки, шрифты** |

**Итог:** PNG-картинка даёт то, что в Telegram доступно нативными средствами — аккуратные таблицы с цветными статусами. Плюс: картинку можно переслать, она не зависит от форматирования клиента. Минус: нельзя скопировать текст из ячейки.

## Сравнение с оригиналом

| | Оригинал (vladimiraldushin) | Этот плагин |
|---|---|---|
| Архитектура | Плагин ✅ | Плагин ✅ |
| Long Polling | ❌ Только Webhook | ✅ Оба режима |
| STT Голос | ❌ | ✅ Встроен |
| Стриминг (edit_message) | ❌ | ✅ |
| **Таблицы-картинки (PNG)** | ❌ | ✅ **Уникально** |
| **Интерактивные кнопки** | ❌ | ✅ model picker, approval, clarify |
| Загрузка файлов | ❌ | ✅ Двухшаговая |
| Разбивка сообщений | ✅ | ✅ Улучшена |
| Извлечение медиа | ✅ | ✅ Расширено |
| Дедупликация сообщений | ❌ | ✅ 300 сек |
| Тесты | ✅ Базовые | ✅ 94 теста |
| Настройка | ✅ | ✅ + STT + табл. |

## Как это работает (архитектура)

```
┌─────────┐     Long Polling / Webhook     ┌─────────────────┐
│  MAX    │ ──────────────────────────────→ │  MaxAdapter     │
│  Client │                                  │  (adapter.py)   │
│  (бот)  │ ←────────────────────────────── │     ↓           │
└─────────┘     POST /messages (текст/PNG)  │  ┌───────────┐  │
                                            │  │ send()    │  │
                                            │  │  ↓        │  │
                                            │  │ tables?   │──┼── MAX_TABLE_AS_IMAGE=true
                                            │  │  ↓   ↓    │  │    → Pillow → PNG
                                            │  │ текст PN  │  │    → POST /uploads
                                            │  │       G   │  │    → PUT → token
                                            │  └───────────┘  │    → POST /messages
                                            │  ┌───────────┐  │
                                            │  │ STT (опц.) │──┼── faster-whisper
                                            │  └───────────┘  │
                                            └─────────────────┘
```

## Быстрый старт

### 1. Установка

```bash
hermes plugins install Realmagnum/hermes-max-integration --enable
```

### 2. Получить токен

Зарегистрироваться на https://business.max.ru/self (юрлицо/ИП/самозанятый РФ).
Создать бота → модерация → **Чат-боты → Перейти → Расширенные настройки → Настроить** → скопировать токен.

### 3. Настройка

```bash
hermes gateway setup
# Выбрать: Max (STT)
```

Или вручную в `~/.hermes/.env`:

```bash
MAX_BOT_TOKEN=ваш_токен
MAX_ALLOWED_USERS=ваш_id_в_max
```

### 4. Включить таблицы-картинки (опционально)

```bash
pip install Pillow
echo 'MAX_TABLE_AS_IMAGE=true' >> ~/.hermes/.env
```

### 5. Перезапуск

```bash
hermes gateway restart
```

## Режимы подключения

Плагин поддерживает два режима получения сообщений от MAX API. Режим определяется единственной переменной — **`MAX_WEBHOOK_URL`**:

| `MAX_WEBHOOK_URL` | Режим | Механизм |
|---|---|---|
| Не задан (пуст) | **Long polling** (по умолчанию) | Цикличный `GET /updates?timeout=5&marker=...` |
| Задан HTTPS URL | **Webhook** | aiohttp сервер на порту 8646, регистрация `POST /subscriptions` |

Выбор происходит в коде `connect()` одной строкой: `self._use_webhook = bool(self._webhook_url)`.

### Переключение Long polling → Webhook

```bash
# 1. Добавить в ~/.hermes/.env
MAX_WEBHOOK_URL=https://your-domain.com/max/webhook
MAX_WEBHOOK_SECRET=my-secret

# 2. Перезапустить
sudo systemctl restart hermes-gateway
```

При старте: `_start_webhook()` → открывает `0.0.0.0:8646` → регистрирует подписку в MAX API → сообщения приходят на webhook URL.

### Переключение Webhook → Long polling

```bash
# 1. Удалить или закомментировать MAX_WEBHOOK_URL (и MAX_WEBHOOK_SECRET)
# MAX_WEBHOOK_URL=...
# MAX_WEBHOOK_SECRET=...

# 2. Перезапустить
sudo systemctl restart hermes-gateway
```

При старте: `_start_polling()` → проверяет `GET /subscriptions`, **автоматически удаляет** старые webhook-подписки (иначе MAX продолжал бы слать сообщения на несуществующий URL) → запускает `_poll_loop`.

### 🚨 Важно

Если webhook-подписка была зарегистрирована **вручную** (curl'ом, не через плагин), автоочистка может её не найти. В таком случае удалите вручную:

```bash
curl -X DELETE "https://platform-api.max.ru/subscriptions?url=<URL>" \
  -H "Authorization: $MAX_BOT_TOKEN"
```

Проверить активные подписки: `GET /subscriptions` с тем же токеном.

### 💬 Отображение reasoning (мысли модели)

При использовании reasoning-моделей (DeepSeek R1, Claude Opus, Gemini Thinking и др.) блок с рассуждениями модели (`💭 **Reasoning:**`) добавляется к финальному ответу автоматически.

Чтобы reasoning появлялся как **отдельное свежее сообщение** (а не edit последнего стриминг-сообщения), добавьте в `~/.hermes/config.yaml`:

```yaml
display:
  platforms:
    max:
      fresh_final_after_seconds: 10
```

Это заставит gateway отправить финальный ответ новым сообщением, если стриминг длился дольше 10 секунд — reasoning попадёт в него целиком. Без этого параметра reasoning добавляется как префикс к последнему edit и может быть незаметен.

## Справочник конфигурации

| Переменная | Обязат. | По умолч. | Описание |
|------------|---------|-----------|----------|
| `MAX_BOT_TOKEN` | ✅ | — | Токен бота |
| `MAX_API_BASE` | ❌ | `https://platform-api.max.ru` | Базовый URL API (документация рекомендует `https://platform-api2.max.ru`) |
| `MAX_WEBHOOK_HOST` | ❌ | `0.0.0.0` | Хост вебхука |
| `MAX_WEBHOOK_PORT` | ❌ | `8646` | Порт вебхука |
| `MAX_WEBHOOK_PATH` | ❌ | `/max/webhook` | Путь вебхука |
| `MAX_WEBHOOK_SECRET` | ❌ | — | Секрет для `X-Max-Bot-Api-Secret` |
| `MAX_WEBHOOK_URL` | ❌ | — | Публичный HTTPS (включает webhook-режим) |
| `MAX_ALLOWED_USERS` | ❌ | — | Белый список пользователей |
| `MAX_ALLOW_ALL_USERS` | ❌ | `false` | Разрешить всех пользователей |
| `MAX_GROUP_ALLOWED_USERS` | ❌ | — | ID пользователей, разрешённых в группах |
| `MAX_GROUP_ALLOWED_CHATS` | ❌ | — | ID групп, разрешённых для бота |
| `MAX_STT_ENABLED` | ❌ | `true` | Автозагрузка голоса для STT |
| `MAX_STT_VENV` | ❌ | `~/.hermes/stt-venv` | Путь к venv для faster-whisper |
| `MAX_TABLE_AS_IMAGE` | ❌ | `false` | Отрисовка таблиц как PNG через Pillow |
| `MAX_HOME_CHANNEL` | ❌ | — | Канал по умолчанию для cron/send_message |
| `MAX_HOME_CHANNEL_NAME` | ❌ | — | Имя канала по умолчанию |
| `MAX_INSECURE_SSL` | ❌ | `false` | Отключить проверку SSL (для тестов) |
| `MAX_CROSS_SESSION` | ❌ | `true` | Кросс-платформенные /sessions и /resume (см. ниже) |

---

## 🌐 Кросс-платформенные сессии

**Зачем:** ядро Hermes по умолчанию показывает сессии только в пределах одной платформы — из MAX видны только MAX-сессии. Это корректно для multi-tenant, но неудобно, когда один пользователь работает с нескольких платформ.

**Как работает:** адаптер перехватывает `/sessions` и `/resume` до ядра, запрашивает `SessionDB` без фильтра платформы и форматирует ответ.

| Команда | Действие | Пример вывода |
|---------|----------|--------------|
| `/sessions` | Последние 15 сессий со всех платформ | `1. 💻 cli — Zabbix deploy...` |
| `/sessions search <q>` | Поиск по всем сессиям | `🔍 Sessions matching "traefik"` |
| `/resume <id>` | Переключиться на любую сессию | (переключает без ошибки) |

**Требование:** для `/resume --all` добавьте `max` в `platforms:` config.yaml:
```yaml
platforms:
  max:
    extra:
      allow_admin_from:
        - "95825064"  # ваш MAX user_id
```

**Отключение:** `MAX_CROSS_SESSION=false` в `.env` — вернёт стандартное поведение ядра (только MAX-сессии).

---

## 👁️ send_action — расширенные статусы

`send_typing()` теперь делегирует `send_action()`, которая поддерживает все статусы MAX API:

| Метод | action | MAX API | Описание |
|-------|--------|---------|----------|
| `send_typing()` | `typing` | `typing_on` | Печатает (по умолчанию) |
| `send_action(cid, "typing_off")` | `typing_off` | `typing_off` | Скрыть индикатор |
| `send_action(cid, "sending_photo")` | `sending_photo` | `sending_photo` | Отправляет фото |
| `send_action(cid, "sending_video")` | `sending_video` | `sending_video` | Отправляет видео |
| `send_action(cid, "sending_audio")` | `sending_audio` | `sending_audio` | Отправляет аудио |
| `send_action(cid, "sending_file")` | `sending_file` | `sending_file` | Отправляет файл |
| `send_action(cid, "read")` | `read` | `read` | Отметить как прочитано |

```python
await adapter.send_action("chat:123", "sending_file")
```

## 🔗 Link-кнопки и send_buttons()

Новый публичный метод `send_buttons()` — отправка сообщений с inline-кнопками любых типов:

```python
await adapter.send_buttons(
    chat_id="chat:123",
    text="Выберите действие:",
    buttons=[
        {"type": "link", "text": "🌐 Открыть сайт", "url": "https://example.com"},
        {"type": "callback", "text": "✅ Подтвердить", "payload": "confirm:123"},
        {"type": "request_contact", "text": "📞 Поделиться номером"},
    ],
)
```

Поддерживаемые типы кнопок:

| type | Параметры | Описание |
|------|-----------|----------|
| `callback` | `text`, `payload` (+ опц. `label`) | Inline callback с payload |
| `link` | `text`, `url` (+ опц. `label`) | Открывает URL |
| `message` | `text`, `payload` (+ опц. `label`) | Отправляет предзаполненное сообщение |
| `request_contact` | `text` (+ опц. `label`) | Запрос контакта |
| `request_geo_location` | `text` (+ опц. `label`) | Запрос геолокации |

Каждая кнопка занимает отдельный ряд (по ширине сообщения). Стандартный лимит MAX — до 10 кнопок на сообщение.

При 3+ кнопках они автоматически нумеруются (`1.`, `2.`, `3.`...) как в теле сообщения, так и на самих кнопках.

Опциональное поле `label` содержит **полный текст описания** для fallback в теле сообщения — в отличие от `text` (который идёт на кнопку и может быть обрезан MAX на мобильных устройствах). Если `label` не указан, используется `text`.

```python
# text — короткое (на кнопку), label — полное (в описание)
{"type": "callback", "text": "Базовый", "label": "Базовый — 500₽/мес, 10GB", "payload": "basic"}
```

Если нужно несколько кнопок в одном ряду — используйте `_post_interactive()` напрямую с готовой структурой рядов.

| Исходный эмодзи | Отображается | Значение | Цвет |
|----------------|--------------|----------|------|
| ✅ | ✓ | Готово / Done | `#16a34a` |
| ❌ | ✗ | Ошибка / Failed | `#dc2626` |
| ⚠️ | ⚠ | На проверке / Warning | `#ea580c` |
| ⏳ / ⌛ | ◷ | Ожидание / Pending | `#ca8a04` |
| ⏳ + schedule | ▶ | Запланировано / Scheduled | `#3b82f6` |
| 🔴 | ● | Критично (красный) | `#dc2626` |
| 🟢 | ● | Хорошо (зелёный) | `#16a34a` |
| 🟡 | ● | Средне (жёлтый) | `#ca8a04` |

Если Pillow не установлен — автопереключение на текстовый `` `code` `` режим.

---

## 📎 Нативная доставка файлов (standalone sender)

Плагин умеет отправлять файлы через `hermes send` без запущенного gateway:

```bash
# Текст + файл (работает без модификации ядра)
hermes send --to max:USER_ID "📄 Отчёт MEDIA:/path/to/report.pdf"

# Несколько файлов
hermes send --to max:USER_ID "📦 Файлы: MEDIA:/tmp/a.pdf MEDIA:/tmp/b.xlsx"

# MEDIA-only (требует опционального патча ядра — см. ниже)
```

**Как это работает:**

1. Core извлекает `MEDIA:`-пути → `media_files: List[Tuple[str, bool]]`
2. Текст сообщения отправляется отдельным `POST /messages`
3. Для каждого файла:
    - `POST /uploads?type=file` → URL для загрузки на CDN
    - Multipart POST на CDN → токен файла
    - `POST /messages` с `attachments: [{"type": "file", "payload": {"token": токен}}]`

Все файлы шлются как `type=file` — CDN MAX не валидирует содержимое, что гарантирует доставку любых безопасных расширений (.txt, .md, .png, .jpg, .mp3, .pdf, .doc, .xlsx и т.д.).

⚠️ **Ограничение MAX CDN:** Расширения `.exe`, `.apk`, `.bat`, `.msi` и другие потенциально опасные блокируются MAX на стороне CDN (HTTP 415 — "File extension is forbidden"). Это ограничение платформы, не обходится из плагина.

Для отправки файлов **внутри сессии** (через gateway) используйте `send_image_file()`, `send_document()`, `send_voice()`, `send_video()` — они используют адаптер с ретраем при `attachment.not.ready`.

### Опциональное улучшение: MEDIA-only в core

По умолчанию `hermes send "MEDIA:/file"` (без текста) блокируется ядром:

```
send_message MEDIA delivery is currently only supported for telegram, discord...
```

Это лечится опциональным скриптом, который добавляет MAX в список поддерживаемых платформ в `tools/send_message_tool.py`:

```bash
python3 scripts/apply-core-fix.py       # применить
python3 scripts/apply-core-fix.py --revert  # откатить
```

После применения:
```bash
hermes send --to max:USER_ID "MEDIA:/tmp/image.png"     # ✅ работает
hermes send --to max:USER_ID "текст MEDIA:/file.pdf"     # ✅ и так работало
```

## Решение проблем

### Бот не отвечает

```bash
hermes gateway status
curl -H "Authorization: ***" https://platform-api.max.ru/me
curl http://localhost:8646/health
```

### Таблицы не стали картинками

```bash
# Проверить что включено
grep MAX_TABLE_AS_IMAGE ~/.hermes/.env

# Проверить Pillow
pip list | grep Pillow

# Проверить логи
grep -i "table\|upload\|pillow" ~/.hermes/logs/gateway.log
```

### SSL ошибки с MAX API

MAX использует сертификаты Минцифры РФ. Для тестирования: `MAX_INSECURE_SSL=true`

### Голос не транскрибируется

```bash
grep MAX_STT_ENABLED ~/.hermes/.env
~/.hermes/stt-venv/bin/pip list | grep faster-whisper
python3 scripts/transcribe_audio.py --latest
```

## Структура проекта

```
hermes-max-integration/
├── plugin.yaml              # Метаданные плагина
├── __init__.py              # register() — точка входа
├── pyproject.toml           # Python-пакет
├── adapter.py               # MaxAdapter (~2600 строк)
├── scripts/
│   ├── apply-core-fix.py      # Опциональный патч core для MEDIA-only
│   └── transcribe_audio.py  # STT транскрипция
├── skills/
│   └── max-gateway/
│       └── SKILL.md         # Навык для AI-агента
├── tests/                   # pytest: 126 тестов
├── AGENTS.md                # Инструкции для AI-агентов
├── after-install.md         # Пост-установка
├── cliff.toml               # git-cliff config (EN)
├── cliff-ru.toml            # git-cliff config (RU)
├── README_EN.md             # Английская версия
├── docs/
│   └── webhook.md           # Архитектура вебхука
└── .github/workflows/ci.yml # CI/CD
```

**Примечание:** сгенерированные PNG-таблицы кэшируются в `~/.hermes/table_images/`.

## Безопасность

| Мера | Детали |
|------|--------|
| 🛡️ **SSRF Защита** | URL загрузок проверяются по белому списку `*.max.ru` / `*.oneme.ru` |
| 🔐 **Токен** | `Authorization` не передаётся при HTTP-редиректах |
| 🔑 **Секрет вебхука** | Сравнение через `secrets.compare_digest` (защита от timing) |
| 🔊 **Приватность голоса** | Аудио-кэш с правами `0700` |
| 🧹 **Чистка ошибок** | Токены и URL удалены из сообщений об ошибках |
| 🔍 **CI** | `bandit` SAST + `pip-audit` при каждом пуше |

Полный аудит и исправления: коммит `e87ee64`.

## История проекта

Проект прошёл две стадии становления.

**Первая версия** была написана с нуля под конкретную задачу: связать Hermes Agent с мессенджером MAX. В ней появились голосовая транскрипция, двухшаговая загрузка файлов, интерактивные кнопки, стриминг ответов — всё то, чего не было в других реализациях.

**Позднее** в поле зрения попал более зрелый проект [vladimiraldushin/hermes-max-platform](https://github.com/vladimiraldushin/hermes-max-platform) — с продуманной архитектурой плагинов, вебхуками, тестами. Вместо того чтобы тянуть две параллельные ветки, было принято решение переработать плагин на его основе:

- Архитектура, подписки (webhook/long polling), система обновлений — из upstream
- Весь наработанный функционал первой версии (STT, таблицы-картинки, кнопки, стриминг, загрузка) — портирован и расширен
- Сверху добавлено то, чего нет ни в одной из исходных веток: отрисовка таблиц в PNG, улучшенный выбор моделей, отдельный отправитель для cron, групповые политики

**В итоге** получился гибрид: надёжный фундамент от upstream плюс функционал, которого нет больше нигде.

## Лицензия

MIT — см. [LICENSE](LICENSE)

## Благодарности

- [vladimiraldushin/hermes-max-platform](https://github.com/vladimiraldushin/hermes-max-platform) — архитектурная основа v2.0 (подписки, вебхуки, структура плагина)
- Оригинальная разработка v1.0 — Realmagnum (STT, таблицы-картинки, кнопки, стриминг, загрузка файлов)
- [Hermes Agent](https://hermes-agent.nousresearch.com/docs) — фреймворк агента
