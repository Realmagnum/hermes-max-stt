# Hermes MAX STT — Плагин для MAX

**Полноценный плагин Hermes Agent** для мессенджера MAX (max.ru) со встроенной голосовой транскрипцией.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Hermes](https://img.shields.io/badge/Hermes-Agent-8A2BE2)](https://hermes-agent.nousresearch.com/docs)

## Возможности

| Функция | Описание |
|---------|----------|
| 🟣 **MAX Messenger** | Полная интеграция шлюза с max.ru |
| 📡 **Два режима** | Long polling (`GET /updates`) + Webhook (`POST /max/webhook`) |
| 🎤 **STT Голос** | Авто-загрузка голосовых сообщений → локальный путь → faster-whisper |
| 📝 **Стриминг** | `edit_message` через `PUT /messages` для потокового вывода токенов |
| ✂️ **Чанкование** | Умная разбивка сообщений по 4000 символов с сохранением абзацев |
| 🖼️ **Таблицы-картинки** | Отрисовка markdown-таблиц как PNG через Pillow с цветными иконками статусов |
| 🔒 **Контроль доступа** | Белый список пользователей, групповые политики, проверка секрета вебхука |
| 📎 **Медиа** | Рекурсивное извлечение вложений, кэширование изображений/документов/аудио |
| ⬆️ **Загрузка** | Двухшаговая загрузка файлов (`POST /uploads` → PUT → токен) |
| ⚡ **Печать** | Индикатор печати для всех типов чатов |
| 🧪 **Тесты** | pytest + pytest-asyncio, 94 теста |
| 🔧 **Интерактивная настройка** | `hermes gateway setup` с подсказками |

## Сравнение

| | Оригинал (vladimiraldushin) | Этот плагин |
|---|---|---|
| Архитектура | Плагин ✅ | Плагин ✅ |
| Long Polling | ❌ Только Webhook | ✅ Оба режима |
| STT Голос | ❌ | ✅ Встроен |
| Стриминг | ❌ | ✅ edit_message |
| Дедупликация | ❌ | ✅ 300 сек |
| Загрузка файлов | ❌ | ✅ Двухшаговая |
| Разбивка сообщений | ✅ | ✅ Улучшена |
| Извлечение медиа | ✅ | ✅ Расширено |
| Тесты | ✅ Базовые | ✅ Расширенные, 94 шт. |
| Настройка | ✅ | ✅ + STT опция |

## Быстрый старт

### 1. Установка

```bash
hermes plugins install Realmagnum/hermes-max-stt --enable
```

### 2. Получить токен бота MAX

1. Зарегистрироваться на https://business.max.ru/self (требуется юрлицо/ИП/самозанятый РФ)
2. Создать бота → пройти модерацию
3. Скопировать токен из **Чат-боты → Перейти → Расширенные настройки → Настроить**

### 3. Настройка

```bash
hermes gateway setup
# Выбрать: Max (STT)
# Следовать интерактивным подсказкам
```

Или вручную в `~/.hermes/.env`:

```bash
MAX_BOT_TOKEN=ваш_токен
MAX_ALLOWED_USERS=ваш_id_в_max
MAX_STT_ENABLED=true
```

### 4. Перезапуск

```bash
hermes gateway restart
```

### 5. Опционально: STT (голосовая транскрипция)

```bash
python3 -m venv ~/.hermes/stt-venv
~/.hermes/stt-venv/bin/pip install faster-whisper

# Скопировать скрипт транскрипции
cp scripts/transcribe_audio.py ~/.hermes/scripts/
```

Для HTTPS вебхука (продакшн) — откройте порт 8646 через Cloudflare Tunnel или Traefik.

### 6. Опционально: Таблицы как картинки

Отрисовка markdown-таблиц в виде PNG-изображений с цветными иконками статусов вместо моноширинного текста.

```bash
# Установить Pillow (обязательно)
pip install Pillow

# Включить в .env
echo 'MAX_TABLE_AS_IMAGE=true' >> ~/.hermes/.env

# Перезапустить шлюз
hermes gateway restart
```

Когда режим включён, адаптер преобразует таблицы в картинки:

```
`-------------------------`
`| Сервер   | Статус     |`
`|----------|------------|`
`| web-01   | ✓ Готово   |`   →  цветной PNG с иконками
`| db-main  | ✗ Ошибка   |`
`-------------------------`
```

Поддерживаемые эмодзи ✅❌⚠️⏳ → цветные Unicode-символы (✓ ✗ ⚠ ◷ ▶ ●) с зелёным/красным/оранжевым/янтарным/синим цветом текста.

| Символ | Значение | Цвет |
|--------|----------|------|
| ✓ Готово | Зелёный `#16a34a` |
| ✗ Ошибка | Красный `#dc2626` |
| ⚠ На проверке | Оранжевый `#ea580c` |
| ◷ Ожидание | Янтарный `#ca8a04` |
| ▶ Запланировано | Синий `#3b82f6` |

Автоматический fallback на текстовый режим, если Pillow не установлен.

## Справочник конфигурации

| Переменная | Обязат. | По умолч. | Описание |
|------------|---------|-----------|----------|
| `MAX_BOT_TOKEN` | ✅ | — | Токен бота из MAX Platform |
| `MAX_WEBHOOK_HOST` | ❌ | `0.0.0.0` | Хост для вебхука |
| `MAX_WEBHOOK_PORT` | ❌ | `8646` | Порт для вебхука |
| `MAX_WEBHOOK_PATH` | ❌ | `/max/webhook` | Путь вебхука |
| `MAX_WEBHOOK_SECRET` | ❌ | — | Секрет для X-Max-Bot-Api-Secret |
| `MAX_WEBHOOK_URL` | ❌ | — | Публичный HTTPS URL (включает режим вебхука) |
| `MAX_ALLOWED_USERS` | ❌ | — | ID разрешённых пользователей (через запятую) |
| `MAX_ALLOW_ALL_USERS` | ❌ | `false` | Разрешить всех пользователей |
| `MAX_STT_ENABLED` | ❌ | `true` | Авто-загрузка голоса для STT |
| `MAX_TABLE_AS_IMAGE` | ❌ | `false` | Отрисовка таблиц как PNG через Pillow |
| `MAX_HOME_CHANNEL` | ❌ | — | Канал по умолчанию для cron/send_message |

## Решение проблем

### Бот не отвечает

1. Проверить статус шлюза: `hermes gateway status`
2. Проверить MAX /me: `curl -H "Authorization: ***" https://platform-api.max.ru/me`
3. Проверить вебхук: `curl http://localhost:8646/health`

### SSL ошибки с MAX API

MAX использует сертификаты Минцифры РФ. Для тестирования:

```bash
MAX_INSECURE_SSL=true
```

### Голос не транскрибируется

1. Проверить `MAX_STT_ENABLED=true` в `.env`
2. Проверить venv: `~/.hermes/stt-venv/bin/pip list | grep faster-whisper`
3. Проверить вручную: `python3 scripts/transcribe_audio.py --latest`

## Структура проекта

```
hermes-max-stt/
├── plugin.yaml              # Метаданные плагина Hermes
├── __init__.py              # register() точка входа
├── pyproject.toml           # Python-пакет
├── adapter.py               # MaxAdapter (~2600 строк)
├── scripts/
│   └── transcribe_audio.py  # STV транскрипция
├── skills/
│   └── max-gateway/
│       └── SKILL.md         # Навык агента
├── tests/                   # pytest тесты (94 шт.)
├── AGENTS.md                # Инструкции для AI-агентов
├── after-install.md         # Пост-установочная инструкция
├── README_EN.md             # Английская версия
└── .github/workflows/ci.yml # CI/CD
```

## Безопасность

Плагин следует принципу secure-by-default:

| Мера | Детали |
|------|--------|
| 🛡️ **SSRF Защита** | URL загрузки файлов проверяются по белому списку `*.max.ru` / `*.oneme.ru` |
| 🔐 **Безопасность токена** | Заголовок `Authorization` никогда не передаётся при HTTP-редиректах |
| 🔑 **Секрет вебхука** | Сравнение через `secrets.compare_digest` (защита от timing-атак) |
| 🔊 **Приватность голоса** | Аудио-кэш с правами `0700` |
| 🧹 **Чистка ошибок** | Токены и URL удаляются из сообщений об ошибках |
| 🔍 **CI усиление** | `bandit` SAST + `pip-audit` сканирование зависимостей при каждом пуше |

Полный аудит и исправления: коммит `e87ee64`.

## Лицензия

MIT — см. [LICENSE](LICENSE)

## Благодарности

- Основано на [vladimiraldushin/hermes-max-platform](https://github.com/vladimiraldushin/hermes-max-platform) — оригинальная архитектура плагина
- [Hermes Agent](https://hermes-agent.nousresearch.com/docs) — фреймворк агента
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — голосовая транскрипция
