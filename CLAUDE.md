# Leonardo — Agent Instructions (v2)

## МИССИЯ
Суперреалистичные AI-фото людей из Японии / Кореи / Китая → автозаливка во все доступные стоки **без API**, через persistent-context браузер.

Пайплайн: **тренд → PhotoBrief → ComfyUI FLUX → MetaCore → per-stock адаптеры → Playwright upload → poll review**

---

## АРХИТЕКТУРА (НЕ МЕНЯТЬ без обновления ARCHITECTURE.md)

```
scheduler/pipeline.py            ← ARQ-воркер, оркестрирует всё
       │
       ├─ parsers/*.py           ← Playwright, без API (Freepik trends, Adobe trending)
       ├─ prompt_engine/
       │     brief_builder.py    ← Trend → PhotoBrief (этничность, сцена, камера)
       │     workflow_builder.py ← PhotoBrief → ComfyUI workflow dict
       │     templates/          ← FLUX-dev JSON шаблоны
       ├─ generation/
       │     comfyui_client.py   ← WS клиент, FLUX-dev Q4 + FaceDetailer + Upscale
       ├─ metadata/
       │     core.py             ← Gemma3n E2B → MetaCore
       │     keyword_expander.py ← правила: добивает kw до 49
       │     adapters/*.py       ← MetaCore → PerStockMeta (per-сток лимиты)
       ├─ uploaders/
       │     base.py             ← BrowserUploader ABC
       │     browser/            ← Playwright sessions, stealth, human-delays, rate-limit
       │     adobe.py | freepik.py | dreamstime.py | depositphotos.py
       ├─ storage/               ← asyncpg, SQLModel, PostgreSQL
       └─ infra/                 ← config, logger, vram_guard
```

**Слои (строго):**
| Слой | Ответственность |
|------|----------------|
| `parsers/` | только парсинг трендов, ничего в БД |
| `prompt_engine/` | trend → brief → workflow JSON, никакого I/O |
| `generation/` | только ComfyUI WS |
| `metadata/` | только generation + per-stock адаптеры |
| `uploaders/` | только браузерные действия (Playwright); никакого API |
| `scheduler/` | оркестрация ARQ + retry |
| `storage/` | все SQL-запросы |
| `infra/` | config, logger, vram_guard |

---

## КЛЮЧЕВЫЕ РЕШЕНИЯ

### Модель генерации
- **FLUX.1-dev GGUF Q4_K_S** через `ComfyUI-GGUF` (~6.5 GB VRAM)
- T5 в FP8, CLIP-L в RAM (offload)
- FaceDetailer (`bbox/face_yolov8m`) + `4x-UltraSharp` для апскейла до ≥4 MP
- FluxGuidance 1.8–2.5 вместо negative prompt

### Этничность
- Слот в `PhotoBrief.ethnicity ∈ {japanese, korean, chinese}`
- Round-robin по этничности и возрастным группам через счётчик в Redis
- **Без** этничных LoRA по умолчанию — FLUX справляется по тексту, лоры портят разнообразие

### Метаданные
- **Gemma 4 E2B** через Ollama (`gemma4:e2b`, Q4_K_M, vision + tools, ~6-7 GB VRAM)
- Vision подтверждён → читает готовый PNG напрямую, fallback не нужен
- `thinking` отключаем параметром Ollama (для тегирования reasoning лишний)
- `keyword_expander.py` добивает до 49 kw правилами (без LLM)
- Per-stock адаптер обрезает/категоризирует под лимиты конкретного стока

### Загрузка — БЕЗ API
- Playwright + persistent user-data-dir в `~/.leonardo/profiles/<stock>/`
- Первый вход — ручной (`python -m uploaders.login <stock>`), 2FA человеком
- `ensure_logged_in()` проверяет селектор кабинета → при отвале кидает `LoginRequiredError` + алерт в Telegram
- Антидетект: playwright-stealth + human-delays + token bucket (≤15/час, ≤80/день на сток)

---

## VRAM ПРАВИЛО (КРИТИЧНО)

```
ComfyUI FLUX-dev Q4    ~6.5 GB   RTX 5060 8 GB
FaceDetailer + Upscale пик ~7 GB
Gemma 4 E2B Q4_K_M     ~6-7 GB   (vision encoder + KV-cache!)
ИТОГО при параллели    >13 GB = гарантированный OOM
```

**Gemma 4 в активной загрузке почти как FLUX.** Они физически не уживаются на 8 GB.
`vram_guard` — **распределённый Redis-lock**, ключ `gpu:rtx5060`. Один на весь стек.

**Межпроектный контракт:** Donatello (WhisperX, Ollama qwen2.5), Jarvis, Michelangelo берут ТОТ ЖЕ ключ. Это не локальный asyncio.Lock — это redis.lock через `infra/vram_guard.py`.

**Переключение режима:**
```python
async with vram_guard.acquire("comfyui"):   # tag для логов, ключ всегда gpu:rtx5060
    image_path = await comfyui_client.generate(workflow)
# выходя из guard: torch.cuda.empty_cache() + выгрузить ComfyUI чекпойнт

async with vram_guard.acquire("gemma"):
    core = await metadata_core.build(brief, image_path, trend)
# выходя из guard: ollama /api/generate с keep_alive: 0 → выгружает модель из VRAM
```

Параметры lock: `timeout=600` (макс. удержание), `blocking_timeout=1800` (макс. ожидание в очереди).
```python
async with vram_guard.acquire("comfyui"):
    image_path = await comfyui_client.generate(workflow)

async with vram_guard.acquire("gemma"):
    core = await metadata_core.build(brief, image_path, trend)
```

Между шагами FLUX → FaceDetailer → Upscale внутри одного workflow — `torch.cuda.empty_cache()` нодой `VRAMCleanup`. При OOM на upscale — fallback на 2x.

**Браузеры мимо guard** (CPU only) — могут идти параллельно с GPU-задачами.

---

## МАТРИЦА СТОКОВ

| Сток | AI | Статус | Заметка |
|------|----|----|---------|
| Adobe Stock | ✅ (пометка «Generative AI») | **P0** | contributor.adobestock.com |
| Freepik Contributor | ✅ (категория AI-generated) | **P0** | низкий порог, быстрый review |
| Dreamstime | ✅ (Editorial AI) | **P0** | до 100 keywords |
| Depositphotos | ✅ (AI-checkbox) | **P0** | 2FA при login |
| 123RF, Vecteezy | ✅ | P1 | |
| Alamy | ⚠️ | P2 | Stockimo flow |
| Shutterstock | ❌ | **LOCKED** | AI запрещён вне SS AI Dataset программы — uploader-заглушка падает с ошибкой |
| Getty/iStock | ❌ | — | категорический запрет |

---

## ФАЙЛОВАЯ СТРУКТУРА

```
Leonardo/
├── CLAUDE.md
├── ARCHITECTURE.md
├── pyproject.toml
├── .env.example
├── parsers/
│   ├── base.py
│   ├── freepik.py
│   └── adobe_trending.py
├── prompt_engine/
│   ├── brief_builder.py
│   ├── workflow_builder.py
│   └── templates/
│       ├── flux_asian_portrait.json
│       ├── flux_asian_lifestyle.json
│       └── flux_product_lifestyle.json
├── generation/
│   └── comfyui_client.py
├── metadata/
│   ├── core.py
│   ├── keyword_expander.py
│   └── adapters/
│       ├── adobe.py
│       ├── freepik.py
│       ├── dreamstime.py
│       └── depositphotos.py
├── uploaders/
│   ├── base.py
│   ├── login.py                ← CLI для ручного первого входа
│   ├── browser/
│   │   ├── session.py
│   │   ├── stealth.py
│   │   ├── human.py
│   │   └── rate_limit.py
│   ├── adobe.py
│   ├── freepik.py
│   ├── dreamstime.py
│   ├── depositphotos.py
│   └── shutterstock.py         ← LOCKED stub
├── scheduler/
│   └── pipeline.py
├── storage/
│   ├── models.py
│   ├── repository.py
│   └── migrations/
│       ├── 001_init.sql
│       └── 002_v2.sql
├── infra/
│   ├── config.py
│   ├── logger.py
│   └── vram_guard.py
└── tests/
    ├── test_parsers.py
    ├── test_brief_builder.py
    ├── test_workflow_builder.py
    ├── test_metadata_adapters.py
    └── test_uploader_selectors.py
```

---

## ПРАВИЛА КОДА

- Python 3.12, async-first
- Type hints обязательны на public-функциях
- `ruff format` (line-length 100), `ruff check`
- Логгер: только `structlog`, никаких `print`
- Секреты: `.env` + pydantic-settings, `chmod 600`
- HTTP: `httpx.AsyncClient`
- Тесты: pytest + pytest-asyncio
- Конвенциональные коммиты, scope = имя модуля (`generation`, `uploaders`, `metadata`)

---

## БАЗА ДАННЫХ

```
postgresql://creator@localhost:5432/leonardo

trends           — спарсенные тренды
jobs             — задачи: brief_json, workflow_json, meta_core_json, image_path, status
upload_results   — per (job, stock): external_id, cabinet_url, review_status, ai_disclosed
stock_accounts   — login state per сток: profile_dir, last_login_ok, quota
```

Миграции:
```bash
psql -U creator leonardo < storage/migrations/001_init.sql
psql -U creator leonardo < storage/migrations/002_v2.sql
```

---

## СЕРВИСЫ

| Сервис | Адрес |
|--------|-------|
| PostgreSQL 16 | localhost:5432 |
| Redis 7 (ARQ + распред. GPU-lock `gpu:rtx5060`) | localhost:6379 |
| ComfyUI | localhost:8188 |
| Ollama (Gemma 4 E2B) | localhost:11434 |

---

## ЗАПУСК

```bash
cd ~/projects/Leonardo && source .venv/bin/activate

# Разовый прогон
python -m scheduler.pipeline --run-once

# ARQ воркер (продакшн)
arq scheduler.pipeline.WorkerSettings

# Ручной первый логин в сток (headed-режим, 2FA вручную)
python -m uploaders.login adobe
python -m uploaders.login freepik
python -m uploaders.login dreamstime
python -m uploaders.login depositphotos

# Smoke-тест селекторов всех стоков
python -m uploaders.login --smoke-all

# Тесты
pytest tests/ -v
```

---

## ПОСЛЕДОВАТЕЛЬНОСТЬ РАБОТЫ

1. `parsers/freepik.py` каждые 6 ч → `storage.repository.save_trends()`
2. `scheduler.build_brief_and_enqueue` → `PhotoBrief` → `Job(status=pending)`
3. `prompt_engine.workflow_builder` → ComfyUI workflow dict
4. `generation.comfyui_client.generate` (vram_guard `comfyui`) → PNG
5. `metadata.core.build` (vram_guard `gemma`) → `MetaCore`
6. `metadata.keyword_expander.expand` → 49 kw
7. `scheduler.fan_out_uploads` → подзадачи `upload_to_stock(job, stock)` на каждый активный сток
8. Per stock: `metadata.adapters.<stock>.to_stock_meta` → `PerStockMeta` → `uploaders.<stock>.upload`
9. `storage.save_upload_result` со ссылкой на кабинет
10. `scheduler.poll_review` раз в сутки → обновляет `review_status`

---

## ЧТО НЕ ДЕЛАТЬ

- **НЕ** запускать ComfyUI и Gemma одновременно (OOM)
- **НЕ** заливать в Shutterstock / Getty / iStock — это бан аккаунта
- **НЕ** заливать без AI-disclosure — это бан аккаунта
- **НЕ** обходить капчи / 2FA автоматически — алерт человеку и стоп
- **НЕ** прятаться за residential-прокси на старте — контрибьютер-активность разрешена
- **НЕ** хардкодить логины/пароли — `.env` + ручной первый вход
- **НЕ** делать sync HTTP / sync I/O в async контексте
- **НЕ** писать SQL вне `storage/`
- **НЕ** добавлять фичи вне ARCHITECTURE.md без обновления его

---

## ФАЗЫ РАЗРАБОТКИ

| Фаза | Что делаем |
|------|-----------|
| **P0 (сейчас)** | infra + storage v2 + Freepik parser + FLUX templates + ComfyUI client + Gemma 4 E2B metadata (vision) + 4 browser-uploaders (Adobe, Freepik, Dreamstime, Depositphotos) + manual login CLI |
| **P1** | poll_review + relogin alerts + 123RF + Vecteezy + token-bucket в Redis + smoke-тесты селекторов |
| **P2** | Alamy (Stockimo) + аналитика approve/reject per (стиль, этничность, сцена) + auto-tuning brief |
| **P3** | Видео-стоки (Pond5) + перераспределение бюджета по доходности per стока |
