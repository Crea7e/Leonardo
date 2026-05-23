# Leonardo — Architecture v2 (read-only source of truth)

## Цель
Автономная фабрика стокового AI-фото:
- генерация **суперреалистичных** портретов и lifestyle-сцен с людьми из Японии, Кореи, Китая
- **ComfyUI + FLUX.1-dev (GGUF Q4_K_S)** на RTX 5060 8 GB
- автозаливка во все доступные стоки **без API**, через persistent-context браузерные сессии (Playwright)
- автозаполнение title / description / keywords / categories / AI-disclosure под формат каждого стока

---

## Диаграмма

```
[ARQ Worker / Cron]
      │
      ▼
 parsers/*.py ──────────────────► PostgreSQL.trends
      │
      ▼
 prompt_engine/brief_builder.py
   tend → PhotoBrief(ethnicity, age, setting, camera, light, lora_stack)
      │
      ▼
 prompt_engine/workflow_builder.py
   PhotoBrief + template → ComfyUI workflow dict
      │
      ▼   ┌─ vram_guard.acquire("comfyui") ─┐
 generation/comfyui_client.py
   FLUX-dev Q4 → FaceDetailer → Upscale 4x → PNG
      │   └────────────────────────────────┘
      ▼
 metadata/core.py
   ┌─ vram_guard.acquire("gemma") ─┐
   Gemma3n E2B (Ollama) → MetaCore(title, desc, keywords, scene, mood)
   └────────────────────────────────┘
      │
      ▼
 metadata/adapters/{stock}.py
   MetaCore → PerStockMeta (категория, лимиты, AI-disclosure)
      │
      ▼
 uploaders/browser/<stock>.py     (CPU only, параллельно)
   Playwright persistent-context → drop file → fill form → submit
      │
      ▼
 storage/repository.py → upload_results, jobs, stock_accounts
      │
      ▼
 scheduler.poll_review  (раз в сутки, читает кабинет контрибьютера)
```

---

## Компоненты

### parsers/
Только сбор трендов из открытых страниц, без API.

- `base.py` — `TrendParser` ABC + `Trend(keyword, score, source, captured_at)`
- `freepik.py` — Playwright, топ-теги Freepik (главный источник, открытее SS)
- `adobe_trending.py` — Playwright, Adobe Stock trending collections
- `pinterest.py` — опционально, тренды Pinterest (мощный сигнал на lifestyle)

Парсеры **ничего не пишут в БД напрямую** — возвращают `list[Trend]`, запись делает scheduler.

### prompt_engine/

Две стадии: тренд → бриф → workflow.

- `brief_builder.py` — `build_brief(trend: Trend, *, ethnicity_pool: list[str]) → PhotoBrief`
  - Семплирует слоты: ethnicity ∈ {japanese, korean, chinese}, регион, возраст, роль, сцена, свет, камера.
  - Гарантия разнообразия: round-robin по этничности и возрастным группам в рамках одной серии трендов (хранится счётчик в Redis).

- `workflow_builder.py` — `build_workflow(brief: PhotoBrief, template: str) → dict`
  - Загружает шаблон из `templates/`, подставляет prompt-строку, LoRA-стек, размеры, seed.

- `templates/`
  - `flux_asian_portrait.json` — 1024×1280, FluxGuidance 2.0, FaceDetailer ON
  - `flux_asian_lifestyle.json` — 1216×832, без FaceDetailer (несколько лиц)
  - `flux_product_lifestyle.json` — 1280×1024, с продуктом в кадре
  - Все используют **FLUX.1-dev GGUF Q4_K_S** через `ComfyUI-GGUF`, T5 в FP8, CLIP-L в RAM.

**Промпт-формула суперреализма:**
```
[camera+lens] [ethnicity, age, role] [action in setting]
[lighting] [mood] amateur photo, slight grain, candid,
visible skin pores, natural imperfections
```
Negative-prompts FLUX не поддерживает — используется `FluxGuidance 1.8–2.5`.

### generation/

- `comfyui_client.py`
  - `async def generate(workflow: dict) → Path`
  - WS `ws://localhost:8188/ws?client_id=...`, polling `GET /history/{prompt_id}`
  - Скачивает PNG в `output/YYYY-MM-DD/<job_id>.png`
  - Обязательно внутри `async with vram_guard.acquire("comfyui")`

**Двухпроходный пайплайн** (внутри одного workflow):
1. Base gen 1024×1280, ~30 шагов
2. FaceDetailer на bbox `face_yolov8m`
3. Upscale `4x-UltraSharp` до ≥4 MP (минимум стоков)

Если OOM на проходе 3 — fallback на 2x-UltraSharp.

### metadata/

- `core.py` — `async def build_core(brief: PhotoBrief, image_path: Path, trend: Trend) → MetaCore`
  - Драйвер: **Gemma 4 E2B** через Ollama (`gemma4:e2b`, Q4_K_M, 5.1B params, vision + tools, ~6-7 GB VRAM в активной загрузке).
  - Принимает PNG → выдаёт описание, тайтл, primary keywords. Vision подтверждён в capabilities, fallback не нужен.
  - `thinking` capability отключаем параметром (для тегирования reasoning не нужен — быстрее ответ).
  - Под `async with vram_guard.acquire("gemma")`.
  - Возвращает `MetaCore(title, description, primary_keywords[20-25], scene, mood, objects, people_count, suggested_category)`.

- `keyword_expander.py` — `def expand(core: MetaCore, trend: Trend) → list[str]`
  - Правилово (без LLM): добивает primary_keywords до 49 шт. через словари синонимов/гипонимов по scene, mood, objects.

- `adapters/`
  - `adobe.py`, `freepik.py`, `dreamstime.py`, `depositphotos.py`
  - `def to_stock_meta(core: MetaCore, keywords: list[str]) → PerStockMeta`
  - Учитывает per-stock лимиты:
    | Сток | Title | Desc | KW | AI-flag |
    |------|-------|------|----|---------|
    | Adobe | ≤200 | — | ≤49 | обязательно «Generative AI» |
    | Freepik | ≤100 | ≤400 | ≤50 | категория «AI-generated» |
    | Dreamstime | ≤100 | ≤200 | ≤100 | Editorial: AI-Generated tag |
    | Depositphotos | ≤200 | — | ≤50 | AI-generated checkbox |

### uploaders/ — **браузерные, без API**

```
uploaders/
  base.py                  # BrowserUploader ABC
  browser/
    session.py             # Playwright launch + persistent context per stock
    stealth.py             # playwright-stealth patches
    human.py               # gaussian delays, mouse jitter, typing rhythm
    rate_limit.py          # per-stock token bucket в Redis
  adobe.py
  freepik.py
  dreamstime.py
  depositphotos.py
  shutterstock.py          # LOCKED — AI запрещён вне SS AI Dataset программы
```

**Каркас `BrowserUploader`:**
```python
class BrowserUploader(ABC):
    stock: ClassVar[str]
    selectors: ClassVar[SelectorMap]

    async def ensure_logged_in(self, page) -> None: ...
    async def upload(self, image: Path, meta: PerStockMeta) -> UploadResult:
        page = await session.page_for(self.stock)
        await self.ensure_logged_in(page)
        await self._goto_upload(page)
        await self._drop_file(page, image)
        await self._fill_form(page, meta)
        await self._submit(page)
        return await self._read_result(page)

    async def poll_review(self, external_id: str) -> ReviewStatus: ...
```

**Persistent auth:**
- `~/.leonardo/profiles/<stock>/` — Chromium user-data-dir (cookies + IndexedDB + localStorage)
- Первый login — **ручной** в headed-режиме (`python -m uploaders.login adobe`)
- 2FA обрабатывает человек один раз, дальше сессия живёт долго
- `ensure_logged_in` проверяет селектор кабинета → при отвале кидает `LoginRequiredError` и шлёт алерт в Telegram-бот (Jarvis)

**Антидетект (минимум, не обход банов):**
- `playwright-stealth` patches
- реальный UA, viewport 1920×1080, locale `ja-JP` / `ko-KR` / `en-US` по аккаунту
- human-delays между действиями (gauss 200–800 мс)
- token bucket: ≤ 15 загрузок/час и ≤ 80/день на каждый сток
- **без** residential-прокси на старте — контрибьютер-активность разрешена, прятаться не от чего

### scheduler/pipeline.py (ARQ)

```python
async def parse_trends(ctx)                    # каждые 6 ч
async def build_brief_and_enqueue(ctx, trend_id)
async def generate_image(ctx, job_id)          # vram_guard("comfyui")
async def enrich_metadata(ctx, job_id)         # vram_guard("gemma")
async def fan_out_uploads(ctx, job_id)         # создаёт N подзадач
async def upload_to_stock(ctx, job_id, stock)  # CPU, параллельно
async def poll_review(ctx)                     # раз в сутки на каждый upload_result
async def relogin_check(ctx)                   # раз в сутки на каждый stock_account
```

`WorkerSettings.functions` содержит все 7 функций. Retry: `upload_to_stock` x3 с экспоненциальным backoff, `generate_image` x2 (только при OOM).

### storage/

`models.py` — SQLModel:
```
Trend(id, source, keyword, score, captured_at, is_processed)
Job(id, trend_id, status, brief_json, workflow_json, meta_core_json,
    image_path, created_at)
UploadResult(id, job_id, stock, external_id, cabinet_url,
             review_status, ai_disclosed, checked_at)
StockAccount(stock PK, login, profile_dir, last_login_ok,
             daily_quota, used_today)
```

`repository.py` — только async через asyncpg.

### infra/

- `config.py` (pydantic-settings):
  ```
  COMFYUI_URL=ws://localhost:8188
  OLLAMA_URL=http://localhost:11434
  OLLAMA_MODEL=gemma4:e2b
  OLLAMA_THINK=false        # отключить thinking для скорости
  DATABASE_URL=postgresql://creator@localhost:5432/leonardo
  REDIS_URL=redis://localhost:6379
  OUTPUT_DIR=~/projects/Leonardo/output
  PROFILES_DIR=~/.leonardo/profiles
  ALERT_BOT_URL=...  # webhook на Jarvis-бота
  ```
- `vram_guard.py` — **распределённый Redis-lock**, не локальный `asyncio.Lock`.
  - Ключ: `gpu:rtx5060` (общий для всех проектов: Leonardo, Donatello, Jarvis, Michelangelo).
  - Suffix-tag в логах: `comfyui` | `gemma` | `whisperx` — для трейсинга, кто держит lock.
  - Реализация — `redis.asyncio.lock.Lock` через redis-py, `timeout=600 c`, `blocking_timeout=1800 c`.
  - Перед выходом из контекста: `torch.cuda.empty_cache()` (для ComfyUI) и `ollama POST /api/generate {model, keep_alive: 0}` (для gemma) — освобождают VRAM до отдачи lock.
  - Браузеры мимо guard (CPU only).
  - **Donatello, Jarvis и Michelangelo обязаны использовать тот же ключ** перед своими GPU-вызовами (WhisperX, Ollama). Это межпроектный контракт, не локальная оптимизация.
- `logger.py` — structlog JSON, level из env.

---

## База данных

```sql
-- migrations/002_v2.sql
ALTER TABLE jobs ADD COLUMN brief_json JSONB;
ALTER TABLE jobs ADD COLUMN meta_core_json JSONB;
ALTER TABLE jobs DROP COLUMN title;
ALTER TABLE jobs DROP COLUMN keywords;
ALTER TABLE jobs DROP COLUMN hashtags;
ALTER TABLE jobs DROP COLUMN category;

ALTER TABLE upload_results ADD COLUMN cabinet_url TEXT;
ALTER TABLE upload_results ADD COLUMN ai_disclosed BOOLEAN DEFAULT TRUE;

CREATE TABLE stock_accounts (
    stock TEXT PRIMARY KEY,
    login TEXT,
    profile_dir TEXT,
    last_login_ok TIMESTAMPTZ,
    daily_quota INT NOT NULL DEFAULT 50,
    used_today INT NOT NULL DEFAULT 0
);
```

---

## Матрица стоков

| Сток | AI разрешён | Метод | Фаза | Заметка |
|------|-------------|-------|------|---------|
| Adobe Stock | ✅ с пометкой «Generative AI» | browser | **P0** | Contributor Portal |
| Freepik Contributor | ✅ категория AI-generated | browser | **P0** | низкий порог входа, быстрый review |
| Dreamstime | ✅ Editorial AI tag | browser | **P0** | до 100 keywords |
| Depositphotos | ✅ с AI-checkbox | browser | **P0** | 2FA на login |
| 123RF | ✅ | browser | P1 | |
| Vecteezy | ✅ | browser | P1 | требует подтверждения качества |
| Alamy | ⚠️ ограничено | browser | P2 | Stockimo flow |
| Shutterstock | ❌ для общей библиотеки | — | **LOCKED** | только через SS AI Dataset (закрытый контракт) |
| Getty/iStock | ❌ | — | — | категорический запрет AI |
| Pond5 | ⚠️ | — | P3 | в основном видео |

---

## Железо и лимиты

| Компонент | VRAM | Время |
|-----------|------|-------|
| ComfyUI FLUX-dev Q4_K_S 1024×1280 | ~6.5 GB | ~60-90 c |
| FaceDetailer + 4x upscale | пик ~7 GB | +20-30 c |
| Gemma 4 E2B Q4_K_M (vision, Ollama) | ~6-7 GB | ~5-15 c |
| Playwright uploader | CPU only | ~30-60 c/сток |

**ВНИМАНИЕ:** Gemma 4 в активной загрузке занимает почти столько же, сколько FLUX. Они **физически не помещаются** одновременно на RTX 5060 8 GB. `vram_guard` — не оптимизация, а единственный способ не получить OOM. Между сменой режима `comfyui` → `gemma` обязателен `torch.cuda.empty_cache()` + ollama keep-alive выгрузка (`/api/generate` с `keep_alive: 0` после последнего запроса в смене).

**Пропускная способность:** ~40-50 фото/день × 4 стока = ~160-200 загрузок/день.

**Правило сериализации:** `comfyui` и `gemma` через `vram_guard`. ComfyUI не запускается одновременно с другими тяжёлыми GPU-задачами (Donatello, Jarvis).

---

## Риски

1. **Бан за AI без disclosure** — главный риск. Матрица AI-флагов выше + ручной аудит метаданных на P0.
2. **FLUX Q4 + FaceDetailer + Upscale в одном проходе** — впритык по VRAM. Между шагами `torch.cuda.empty_cache()`, fallback на 2x upscale при OOM.
3. **Селекторы порталов ломаются** — `selectors.py` отдельный модуль; smoke-тест «зашёл → форма загрузки видна» раз в сутки на каждый сток.
4. **2FA при ре-логине** — agent не решает, шлёт алерт в Telegram, человек логинится сам.
5. **Изменения политик стоков** — раз в квартал проверять AI-policy каждого активного стока.

---

## Фазы

| Фаза | Deliverable |
|------|------------|
| **P0** | infra + storage v2 + Freepik parser + FLUX templates + ComfyUI client + Gemma metadata + 4 browser-uploaders (Adobe, Freepik, Dreamstime, Depositphotos) + manual login flow |
| **P1** | poll_review + relogin alerts + 123RF + Vecteezy + token-bucket rate limit |
| **P2** | Alamy (Stockimo) + аналитика approve/reject + auto-tuning промпта по acceptance rate |
| **P3** | видео-стоки (Pond5), автоподбор brief-слотов по доходности per стока |
