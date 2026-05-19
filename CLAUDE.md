# Raphael — Agent Instructions

## МИССИЯ
Автономный пайплайн автораскрутки фотостоков:
**тренды → ComfyUI генерация → метаданные → загрузка**

---

## АРХИТЕКТУРА (НЕ МЕНЯТЬ)

```
scheduler/pipeline.py   ← ARQ-воркер, оркестрирует весь цикл
       │
       ├─ parsers/*.py          ← парсинг трендов (Playwright, Shutterstock/Adobe/Freepik)
       ├─ prompt_engine/        ← тренд → ComfyUI workflow JSON
       ├─ generation/           ← WebSocket клиент ComfyUI
       ├─ metadata/             ← title + keywords + hashtags (Gemma via Ollama)
       ├─ uploaders/*.py        ← загрузка в каждый сток через REST API
       └─ storage/              ← asyncpg, SQLModel-модели, PostgreSQL
```

**Слои (строго соблюдать):**
| Слой | Ответственность |
|------|----------------|
| `parsers/` | только парсинг, ничего не пишут в БД напрямую |
| `prompt_engine/` | только трансформация тренда в workflow JSON |
| `generation/` | только общение с ComfyUI WS API |
| `metadata/` | только генерация title/keywords/hashtags |
| `uploaders/` | только HTTP к API фотостоков |
| `scheduler/` | оркестрация, очередь ARQ, retry-логика |
| `storage/` | все SQL-запросы, модели |
| `infra/` | config, logger, vram_guard |

---

## ФАЙЛОВАЯ СТРУКТУРА

```
Raphael/
├── CLAUDE.md                   ← этот файл (инструкции агентам)
├── ARCHITECTURE.md             ← детальная архитектура (read-only)
├── pyproject.toml
├── .env.example
├── parsers/
│   ├── __init__.py
│   ├── base.py                 ← TrendParser ABC + Trend dataclass
│   ├── shutterstock.py         ← Playwright scraper
│   ├── adobe_stock.py          ← Playwright scraper
│   └── freepik.py              ← Playwright scraper
├── prompt_engine/
│   ├── __init__.py
│   ├── builder.py              ← build_workflow(trend) → dict
│   └── templates/              ← SDXL workflow JSON шаблоны
│       └── sdxl_base.json
├── generation/
│   ├── __init__.py
│   └── comfyui_client.py       ← async WS client, возвращает Path к PNG
├── metadata/
│   ├── __init__.py
│   ├── tagger.py               ← Gemma → title + 50 keywords
│   └── hashtags.py             ← хештеги из тренда + keywords
├── uploaders/
│   ├── __init__.py
│   ├── base.py                 ← StockUploader ABC
│   ├── shutterstock.py         ← SS Contributor API v1
│   └── adobe_stock.py          ← Adobe Upload API
├── scheduler/
│   ├── __init__.py
│   └── pipeline.py             ← ARQ worker functions
├── storage/
│   ├── __init__.py
│   ├── models.py               ← SQLModel: Trend, Job, UploadResult
│   ├── repository.py           ← async def get/save/update
│   └── migrations/
│       └── 001_init.sql
├── infra/
│   ├── __init__.py
│   ├── config.py               ← pydantic-settings
│   ├── logger.py               ← structlog JSON
│   └── vram_guard.py           ← asyncio.Lock для RTX 5060
└── tests/
    ├── test_parsers.py
    ├── test_prompt_engine.py
    └── test_metadata.py
```

---

## ПРАВИЛА КОДА

- Python 3.12, async-first для всего ввода-вывода
- Type hints обязательны на всех public функциях
- Форматёр: `ruff format` (line-length 100), линтер: `ruff check`
- Логгер: только `structlog`, никаких `print`
- Секреты: только через `.env` + `pydantic-settings`, никогда в коде
- HTTP клиент: `httpx.AsyncClient`, не `requests`
- Тесты: pytest + pytest-asyncio

---

## VRAM ПРАВИЛО (КРИТИЧНО)

```
ComfyUI SDXL  ~5-6 GB  RTX 5060
Gemma (Ollama) ~3.5 GB  RTX 5060
ИТОГО          ~9 GB    > 8 GB = OOM
```

**Gemma и ComfyUI НИКОГДА не работают одновременно.**
Всегда оборачивай вызовы в `infra/vram_guard.py`:

```python
async with vram_guard.acquire("comfyui"):
    image_path = await comfyui_client.generate(workflow)

async with vram_guard.acquire("gemma"):
    meta = await tagger.generate(trend)
```

---

## БАЗА ДАННЫХ

```
postgresql://creator@localhost:5432/raphael

Таблицы:
  trends        — спарсенные тренды (keyword, source, score)
  jobs          — задачи генерации (trend_id, workflow_json, status, image_path)
  upload_results — результаты загрузок (job_id, stock, external_id, review_status)
```

Запуск миграций: `psql -U creator raphael < storage/migrations/001_init.sql`

---

## СЕРВИСЫ

| Сервис | Адрес |
|--------|-------|
| PostgreSQL 16 | localhost:5432 |
| Redis 7 (ARQ очередь) | localhost:6379 |
| ComfyUI | localhost:8188 |
| Ollama (Gemma) | localhost:11434 |

---

## ЗАПУСК

```bash
cd ~/projects/Raphael && source .venv/bin/activate

# Разовый прогон пайплайна
python -m scheduler.pipeline --run-once

# ARQ воркер (продакшн)
arq scheduler.pipeline.WorkerSettings

# Тесты
pytest tests/ -v
```

---

## ПОСЛЕДОВАТЕЛЬНОСТЬ РАБОТЫ (шаг за шагом)

1. `parsers/*.py` — каждые 6 часов парсят топ-тренды → `storage.repository.save_trends()`
2. `scheduler/pipeline.py` — берёт новые тренды из БД, создаёт `Job` со статусом `pending`
3. `prompt_engine/builder.py` — `build_workflow(trend)` → ComfyUI workflow dict
4. `generation/comfyui_client.py` — генерирует PNG (через `vram_guard`)
5. `metadata/tagger.py` — Gemma генерирует title + keywords (через `vram_guard`)
6. `metadata/hashtags.py` — строит список хештегов
7. `uploaders/*.py` — загружают PNG + метадата на каждый сток
8. `storage/repository.py` — сохраняет `UploadResult`, обновляет статус `Job`

---

## ЧТО НЕ ДЕЛАТЬ

- **НЕ** запускать ComfyUI и Gemma одновременно (OOM)
- **НЕ** хардкодить API ключи
- **НЕ** делать sync HTTP запросы в async контексте
- **НЕ** писать SQL напрямую в слоях выше `storage/`
- **НЕ** добавлять фичи вне ARCHITECTURE.md без обновления этого файла
- **НЕ** публиковать контент без проверки `review_status == approved`
- **НЕ** трогать `~/projects/jarvis/` — это отдельный проект

---

## ФАЗЫ РАЗРАБОТКИ

| Фаза | Что делаем |
|------|-----------|
| **P0 (сейчас)** | infra, storage, один парсер (SS), ComfyUI client, один uploader |
| **P1** | Все парсеры, metadata/tagger, retry логика, ARQ scheduler |
| **P2** | Adobe Stock uploader, аналитика review_status, алерты на сломанные парсеры |
| **P3** | Видео (WAN/Kling), автоподбор параметров по performance |
