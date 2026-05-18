# Raphael — Architecture (read-only source of truth)

## Цель
Автономная система авторасскрутки фотостоков:
- парсинг трендов из Shutterstock / Adobe Stock / Freepik
- генерация фото через ComfyUI SDXL
- автоматическая загрузка с title, keywords, hashtags

---

## Диаграмма

```
[Cron / ARQ Worker]
       │
       ▼
  parsers/*.py ─────────────► PostgreSQL (trends)
       │                              │
       │                              ▼
       │                    scheduler/pipeline.py
       │                         │         │
       │            ┌────────────┘         └────────────┐
       │            ▼                                   ▼
       │   prompt_engine/builder.py          storage/repository.py
       │            │                                   ▲
       │            ▼                                   │
       │   generation/comfyui_client.py ───────────────►│
       │        (RTX 5060, VRAM guard)                  │
       │            │                                   │
       │            ▼                                   │
       │   metadata/tagger.py (Gemma/Ollama)            │
       │   metadata/hashtags.py                         │
       │            │                                   │
       │            ▼                                   │
       │   uploaders/*.py ─────────────────────────────►│
       │   (SS API / Adobe API)                         │
       │                                                │
       └────────────────────────────────────────────────┘
```

---

## Компоненты

### parsers/
- `base.py` — `TrendParser` ABC + `Trend(keyword, score, source, captured_at)`
- `shutterstock.py` — Playwright, парсит `/trending-searches` и категории
- `adobe_stock.py` — Playwright, парсит популярные коллекции
- `freepik.py` — Playwright, топ-теги

Каждый парсер возвращает `list[Trend]`. Никакой записи в БД внутри парсера.

### prompt_engine/
- `builder.py` — `build_workflow(trend: Trend, style: str = "photorealistic") → dict`
  - загружает `templates/sdxl_base.json`
  - подставляет positive prompt из тренда + негатив
  - возвращает готовый ComfyUI workflow dict

- `templates/sdxl_base.json` — базовый SDXL workflow:
  - KSampler: steps=30, cfg=7, sampler=dpmpp_2m
  - размер: 1024×1024 (стандарт для стоков)

### generation/
- `comfyui_client.py`
  - `async def generate(workflow: dict) → Path`
  - WS соединение к `ws://localhost:8188/ws?client_id=...`
  - polling `GET /history/{prompt_id}` до завершения
  - скачивает PNG в `~/projects/Raphael/output/`
  - обязательно под `vram_guard.acquire("comfyui")`

### metadata/
- `tagger.py`
  - `async def generate_metadata(trend: Trend, image_path: Path) → ImageMeta`
  - Ollama `/api/chat` с `gemma4:e2b`
  - возвращает `ImageMeta(title: str, keywords: list[str], category: str)`
  - ≤50 keywords (лимит Shutterstock)
  - обязательно под `vram_guard.acquire("gemma")`

- `hashtags.py`
  - `def build_hashtags(trend: Trend, keywords: list[str]) → list[str]`
  - правило-ориентированная генерация (без LLM)
  - max 30 хештегов

### uploaders/
- `base.py` — `StockUploader` ABC
  ```python
  async def upload(image_path: Path, meta: ImageMeta) -> UploadResult
  ```
- `shutterstock.py` — Shutterstock Contributor API v1
  - `POST /v1/images` — загрузка
  - `PUT /v1/images/{id}/metadata` — метаданные
- `adobe_stock.py` — Adobe Stock Upload API
  - Chunked multipart upload

### scheduler/pipeline.py
ARQ (Redis-based) воркер:

```python
async def parse_trends(ctx):     # каждые 6 часов
async def process_job(ctx, job_id):  # для каждого тренда
async def check_review_status(ctx):  # каждые 24 часа
```

`WorkerSettings` с очередями: `trends`, `generation`, `upload`

### storage/
- `models.py` — SQLModel модели:
  ```
  Trend(id, source, keyword, score, captured_at, is_processed)
  Job(id, trend_id, status, workflow_json, image_path, title, keywords, created_at, uploaded_at)
  UploadResult(id, job_id, stock, external_id, review_status, checked_at)
  ```

- `repository.py` — только async функции через asyncpg:
  ```python
  async def save_trends(trends: list[Trend]) -> None
  async def get_pending_jobs() -> list[Job]
  async def update_job_status(job_id: int, status: str) -> None
  async def save_upload_result(result: UploadResult) -> None
  ```

### infra/
- `config.py`:
  ```
  COMFYUI_URL=ws://localhost:8188
  OLLAMA_URL=http://localhost:11434
  DATABASE_URL=postgresql://creator@localhost:5432/raphael
  REDIS_URL=redis://localhost:6379
  SS_CLIENT_ID=...
  SS_CLIENT_SECRET=...
  ADOBE_CLIENT_ID=...
  ADOBE_CLIENT_SECRET=...
  OUTPUT_DIR=~/projects/Raphael/output
  ```

- `vram_guard.py`:
  ```python
  # Единственный Lock на весь RTX 5060
  # acquire("comfyui") и acquire("gemma") — взаимоисключающие
  ```

- `logger.py` — structlog, JSON формат, level из env

---

## База данных

```sql
-- migrations/001_init.sql
CREATE TABLE trends (
    id SERIAL PRIMARY KEY,
    source VARCHAR(50) NOT NULL,  -- shutterstock|adobe|freepik
    keyword VARCHAR(255) NOT NULL,
    score FLOAT,
    captured_at TIMESTAMPTZ DEFAULT NOW(),
    is_processed BOOLEAN DEFAULT FALSE
);

CREATE TABLE jobs (
    id SERIAL PRIMARY KEY,
    trend_id INT REFERENCES trends(id),
    status VARCHAR(50) DEFAULT 'pending',  -- pending|generating|uploading|done|failed
    workflow_json JSONB,
    image_path TEXT,
    title TEXT,
    keywords TEXT[],
    hashtags TEXT[],
    category VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    uploaded_at TIMESTAMPTZ
);

CREATE TABLE upload_results (
    id SERIAL PRIMARY KEY,
    job_id INT REFERENCES jobs(id),
    stock VARCHAR(50) NOT NULL,  -- shutterstock|adobe
    external_id VARCHAR(255),
    review_status VARCHAR(50) DEFAULT 'pending',  -- pending|approved|rejected
    checked_at TIMESTAMPTZ
);
```

---

## Железо и лимиты

| Компонент | VRAM | Время |
|-----------|------|-------|
| ComfyUI SDXL 1024×1024 | 5-6 GB | ~45 сек |
| Gemma 4:e2b (Ollama) | 3.5 GB | ~10 сек |
| Playwright parser | CPU only | ~5 сек/сток |

**Пропускная способность:** ~60-80 фото/день (при генерации 24/7)

---

## Фазы

| Фаза | Deliverable |
|------|------------|
| P0 | infra + storage + SS парсер + ComfyUI client + SS uploader |
| P1 | все парсеры + metadata/tagger + ARQ scheduler + retry |
| P2 | Adobe uploader + review monitoring + alerts |
| P3 | видео (WAN/Kling) + auto-tuning по performance |
