# Hermes MAX Gateway

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
| 🔘 **Интерактивные кнопки** | Выбор модели (`/model`), подтверждение команд (approval), уточнения (clarify) |
| ✂️ **Авточанкование** | Умная разбивка длинных сообщений (до 4000 символов с сохранением абзацев) |
| ⬆️ **Загрузка файлов** | Двухшаговая загрузка: `POST /uploads` → PUT → токен → отправка |
| 🔒 **Контроль доступа** | Белый список пользователей, групповые политики, проверка секрета вебхука |
| 📎 **Медиа** | Рекурсивное извлечение вложений, кэш изображений/документов/аудио |
| 🎞️ **Голосовые/Видео/Документы** | Отдельные методы `send_voice`, `send_video`, `send_document` |
| ⚡ **Индикатор ввода** | Отображение набора текста для всех типов чатов |
| 🔧 **Standalone-отправитель** | Отправка сообщений из cron/send_message через `_send_max_message` |
| 🧪 **Тесты** | pytest + pytest-asyncio, **94 теста** |
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
hermes plugins install Realmagnum/hermes-max-stt --enable
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

## Справочник конфигурации

| Переменная | Обязат. | По умолч. | Описание |
|------------|---------|-----------|----------|
| `MAX_BOT_TOKEN` | ✅ | — | Токен бота |
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

## Символы в таблицах-картинках

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
hermes-max-stt/
├── plugin.yaml              # Метаданные плагина
├── __init__.py              # register() — точка входа
├── pyproject.toml           # Python-пакет
├── adapter.py               # MaxAdapter (~2600 строк)
├── scripts/
│   └── transcribe_audio.py  # STT транскрипция
├── skills/
│   └── max-gateway/
│       └── SKILL.md         # Навык для AI-агента
├── tests/                   # pytest: 94 теста
├── AGENTS.md                # Инструкции для AI-агентов
├── after-install.md         # Пост-установка
├── README_EN.md             # Английская версия
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

## Лицензия

MIT — см. [LICENSE](LICENSE)

## Благодарности

- [vladimiraldushin/hermes-max-platform](https://github.com/vladimiraldushin/hermes-max-platform) — оригинальная архитектура
- [Hermes Agent](https://hermes-agent.nousresearch.com/docs) — фреймворк
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — транскрипция
