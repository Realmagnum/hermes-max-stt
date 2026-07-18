# Webhook в hermes-max-stt

Подробное описание архитектуры, запуска, обработки запросов и безопасности вебхук-сервера.

---

## 1. Архитектура

```
MAX Cloud               Твой сервер                    Hermes Gateway
─────────────          ─────────────                   ──────────────
                       ┌──────────────┐
Пользователь           │ Reverse Proxy │               ┌────────────┐
написал боту ─────────→│ (Traefik)    │──→ :8646 ──→  │ MaxAdapter │
                       │ TLS terminate│               │ (aiohttp)  │
MAX API ◀──────────────│              │               └─────┬──────┘
(отправляет            └──────────────┘                     │
 callback)                                                 ↓
                                              ┌─────────────────────┐
                                              │ asyncio.Queue       │
                                              │ → handle_message()  │
                                              │ → Agent.process()   │
                                              └─────────────────────┘
```

---

## 2. Конфигурация

Вебхук активируется **только** когда задан `MAX_WEBHOOK_URL` — публичный HTTPS-адрес:

```bash
# ~/.hermes/.env
MAX_BOT_TOKEN=***
MAX_WEBHOOK_URL=https://max.rmg7.com/max/webhook
MAX_WEBHOOK_SECRET=***    # опционально, но строго рекомендуется
MAX_WEBHOOK_HOST=0.0.0.0               # на каком IP слушать (по умолчанию все)
MAX_WEBHOOK_PORT=8646                  # порт (по умолчанию 8646)
MAX_WEBHOOK_PATH=/max/webhook          # URL-путь для приёма колбэков
```

Без `MAX_WEBHOOK_URL` адаптер работает в режиме **long polling** — сам ходит в MAX API каждые 5 секунд.

---

## 3. Запуск: пошагово

```
connect()
  │
  ├─ 1. Проверка токена → GET https://platform-api.max.ru/me
  │     └─ 401 → fatal error, стоп
  │     └─ 200 → ок, читаем user_id/username
  │
  ├─ 2. self._use_webhook == True? → _start_webhook()
  │     │
  │     ├─ 2a. Проверка: aiohttp установлен?
  │     │
  │     ├─ 2b. Проверка: порт 8646 свободен?
  │     │     └─ connect(127.0.0.1, 8646) — если отвечает → порт занят, ошибка
  │     │
  │     ├─ 2c. Создаём aiohttp Application
  │     │     ├─ GET  /health       → {"status": "ok"}
  │     │     └─ POST /max/webhook  → webhook_handler()
  │     │
  │     ├─ 2d. Стартуем сервер → web.TCPSite(host=0.0.0.0, port=8646)
  │     │
  │     ├─ 2e. Авторегистрация в MAX API:
  │     │     POST https://platform-api.max.ru/subscriptions
  │     │     Body: {
  │     │       "url": "https://max.rmg7.com/max/webhook",
  │     │       "secret": "***",
  │     │       "update_types": ["message_created", "message_callback",
  │     │                        "bot_started", "bot_added"]
  │     │     }
  │     │
  │     └─ 2f. Запускаем _queue_poll_loop() — разгребает очередь сообщений
  │
  └─ 3. _mark_connected() → адаптер готов
```

---

## 4. Обработка входящего запроса

Когда пользователь пишет боту, MAX API делает POST на вебхук:

```
POST https://max.rmg7.com/max/webhook
Header: X-Max-Bot-Api-Secret: my-secret-abc123
Body: {
  "update_type": "message_created",
  "message": {
    "sender": {"user_id": 42, "name": "Шеф"},
    "recipient": {"chat_type": "dialog"},
    "body": {"mid": "msg-001", "text": "Привет!"}
  }
}
```

### Обработчик `webhook_handler()` — 5 защитных слоёв:

| Шаг | Что | Отказ → |
|-----|-----|---------|
| 🛡️ **Rate limit** | Считаем запросы с IP. >30 за 10с? | **429** |
| 🔑 **Secret** | `X-Max-Bot-Api-Secret` == `MAX_WEBHOOK_SECRET`? | **403** |
| 📦 **JSON parse** | Тело — валидный JSON? | **400** |
| 🏗️ **Build event** | `_build_event(payload)` — парсим, dedup, access control | **nil** (тихо) |
| 📬 **Enqueue** | Кладём `MessageEvent` в `asyncio.Queue` | — |

```python
# Упрощённая логика:
async def webhook_handler(req):
    # 1. Rate limit (30 req/10s с одного IP)
    if too_many_requests(req.remote):
        return 429

    # 2. Secret verification
    body = await req.read()
    if not secrets.compare_digest(req.headers["X-Max-Bot-Api-Secret"], my_secret):
        return 403

    # 3. JSON parse
    payload = json.loads(body)

    # 4. Build event (dedup, access control, media extraction, STT)
    event = await self._build_event(payload)
    if event is None:  # дубликат, бот, неавторизован...
        return 200      # тихо игнорируем

    # 5. В очередь на обработку
    await self._message_queue.put(event)
    return 200
```

---

## 5. Что происходит после вебхука

Очередь разгребает `_queue_poll_loop()`:

```
_message_queue ──→ handle_message(event)
                      │
                      └──→ Gateway.process_message()
                              │
                              └──→ Agent.run()
                                      │
                                      └──→ Ответ через send() → MAX API
```

Сообщение **не обрабатывается внутри webhook-хендлера** — хендлер максимально быстрый (только валидация + enqueue), чтобы не блокировать aiohttp event loop и не терять запросы.

---

## 6. Типы обрабатываемых update_type

| update_type | Что делает адаптер |
|-------------|-------------------|
| `message_created` | Парсит текст/медиа → `MessageEvent` |
| `message_edited` | То же самое (MAX не различает) |
| `message_callback` | Нажатие инлайн-кнопки → `_on_callback()` |
| `bot_started` | `/start` → сохраняет user_id в `_dm_user_ids` |
| `bot_added` | Бота добавили в группу → `/start` (internal) |

---

## 7. Dedup (защита от дублей)

MAX иногда шлёт одно сообщение дважды. Адаптер хранит `_seen_msgs: {mid → timestamp}`:

- Если `mid` уже в словаре И прошло < 300 секунд → игнорируем (None)
- Иначе → запоминаем `mid`, чистим старые (>300с), чистим если >5000 записей

---

## 8. Отключение

```python
async def disconnect():
    self._running = False
    self._stop.set()
    # Останавливаем webhook-сервер
    await self._webhook_runner.cleanup()
    # Закрываем HTTP-клиент
    await self._http_client.aclose()
    # Отменяем фоновые задачи
    for task in self._background_tasks:
        task.cancel()
```

---

## 9. Long Polling vs Webhook

| | Long Polling | Webhook |
|---|---|---|
| **Как работает** | Адаптер сам ходит в MAX API (`GET /updates`) | MAX API стучится к адаптеру (`POST /webhook`) |
| **Задержка** | До 5 секунд | Мгновенно |
| **Нужен HTTPS** | Нет | **Да** (требование MAX) |
| **Нужен публичный URL** | Нет | Да (Traefik/Cloudflare/ngrok) |
| **Когда использовать** | Разработка, тесты | Production |
| **Активация** | По умолчанию (без `MAX_WEBHOOK_URL`) | При заданном `MAX_WEBHOOK_URL` |

---

## 10. Безопасность: итоговая картина

```
Внешний мир
    │
    ▼
┌──────────────────────┐
│ Reverse Proxy (TLS)  │  ← терминирует HTTPS
│ Traefik / nginx      │
└────────┬─────────────┘
         │ HTTP (внутренняя сеть)
         ▼
┌──────────────────────┐
│ 0.0.0.0:8646         │
│ aiohttp webhook      │
│                      │
│ 🛡️ Rate limit: 30/10s│  ← защита от DoS
│ 🔑 Secret verify     │  ← только MAX API
│ 📦 JSON validate     │  ← не сломать парсер
│ 🏗️ Dedup: 300s       │  ← не задвоить сообщения
│ 👤 Access control    │  ← только whitelist
└────────┬─────────────┘
         │
         ▼
    asyncio.Queue → Gateway
```
