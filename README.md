# Raphael

Автономный пайплайн авторасскрутки фотостоков.

---

## Цель

Пассивный доход с фотостоков без ручного труда.

Система каждые 6 часов:
1. Парсит тренды на Shutterstock / Adobe Stock / Freepik
2. Генерирует фото под эти тренды через ComfyUI SDXL (1024×1024)
3. Пишет title + 50 keywords + hashtags через Gemma (local)
4. Загружает на стоки через Contributor API

**Целевая пропускная способность:** 60–80 фото/день без участия человека.

---

## Архитектура

```
[ARQ Cron: каждые 6ч]
       │
       ▼
parsers/                     ← Playwright парсит тренды с сайтов стоков
       │
       ▼ trend → PostgreSQL
scheduler/pipeline.py        ← оркестрирует очередь через Redis/ARQ
       │
       ├─► prompt_engine/builder.py      ← тренд → ComfyUI workflow JSON
       │            │
       │            ▼
       ├─► generation/comfyui_client.py  ← WebSocket → ComfyUI → PNG
       │            │  (RTX 5060, vram_guard Lock)
       │            ▼
       ├─► metadata/tagger.py            ← Gemma → title + 50 keywords
       │   metadata/hashtags.py          ← хештеги без LLM
       │            │  (vram_guard — строго после ComfyUI, не одновременно)
       │            ▼
       └─► uploaders/shutterstock.py     ← Contributor API v1
           uploaders/adobe_stock.py      ← Adobe Upload API
                    │
                    ▼
             storage/repository.py → PostgreSQL (trends, jobs, upload_results)
```

---

## Ключевые решения

| Решение | Почему |
|---------|--------|
| ARQ (Redis) вместо cron | retry из коробки, состояние каждого job в БД |
| `vram_guard` asyncio.Lock | ComfyUI (5-6 GB) + Gemma (3.5 GB) > 8 GB VRAM — только поочерёдно |
| Playwright для парсинга | стоки JS-heavy, статический scraper не работает |
| Gemma через Ollama (local) | нет расходов на API, работает офлайн |
| 3 таблицы в PostgreSQL | `trends` → `jobs` → `upload_results`, каждый статус трекается |

---

## Структура проекта

```
Raphael/
├── parsers/             ← парсеры трендов (Playwright)
│   ├── base.py          ← TrendParser ABC + Trend dataclass
│   ├── shutterstock.py
│   ├── adobe_stock.py
│   └── freepik.py
├── prompt_engine/       ← тренд → ComfyUI workflow
│   ├── builder.py
│   └── templates/       ← SDXL workflow JSON шаблоны
├── generation/          ← WebSocket клиент ComfyUI
│   └── comfyui_client.py
├── metadata/            ← генерация метаданных
│   ├── tagger.py        ← Gemma: title + keywords
│   └── hashtags.py      ← правило-ориентированные хештеги
├── uploaders/           ← загрузка на стоки
│   ├── base.py          ← StockUploader ABC
│   ├── shutterstock.py
│   └── adobe_stock.py
├── scheduler/           ← ARQ воркер, оркестрация
│   └── pipeline.py
├── storage/             ← PostgreSQL: модели, репозиторий, миграции
│   ├── models.py
│   ├── repository.py
│   └── migrations/001_init.sql
├── infra/               ← config, logger, vram_guard
├── tests/
├── CLAUDE.md            ← инструкции для агентов
├── ARCHITECTURE.md      ← детальная техническая документация
├── pyproject.toml
└── .env.example
```

---

## База данных

```sql
trends        — спарсенные тренды (keyword, source, score)
jobs          — задачи генерации (trend_id, status, image_path, title, keywords)
upload_results — результаты загрузок (job_id, stock, external_id, review_status)
```

```bash
# Создать БД и применить миграцию
createdb -U creator raphael && psql -U creator raphael < storage/migrations/001_init.sql
```

---

## Сервисы

| Сервис | Адрес |
|--------|-------|
| PostgreSQL 16 | localhost:5432 |
| Redis 7 | localhost:6379 |
| ComfyUI | localhost:8188 |
| Ollama (Gemma) | localhost:11434 |

---

## Запуск

```bash
cd ~/projects/Raphael && source .venv/bin/activate

# Разовый прогон
python -m scheduler.pipeline --run-once

# ARQ воркер (продакшн)
arq scheduler.pipeline.WorkerSettings

# Тесты
pytest tests/ -v
```

---

## Фазы разработки

| Фаза | Deliverable |
|------|------------|
| **P0** (текущая) | SS парсер + ComfyUI клиент + metadata + SS uploader — полный цикл |
| **P1** | Adobe + Freepik парсеры, retry, мониторинг review_status |
| **P2** | Алерты на сломанные парсеры, дашборд статистики |
| **P3** | Видео (WAN/Kling), авто-подбор стилей по одобренным работам |
