# AGENTS.md — Leonardo (фотостоки)

Этот файл читают все AI-агенты (Claude Code, aider, Cline, Roo) перед правкой кода в этом репо.
Глобальные правила — в [`~/projects/AGENTS.md`](../AGENTS.md).

---

## TL;DR проекта

Leonardo = автономная монетизация фотостоков.
Цикл: парсинг трендов → генерация фото через ComfyUI SDXL → метаданные через Gemma → загрузка на Shutterstock / Adobe Stock.

```
[ARQ cron 6ч] → parsers/ → PostgreSQL → scheduler/pipeline.py
                                              │
                              prompt_engine/ → generation/ → metadata/ → uploaders/
                              (workflow JSON)   (ComfyUI)    (Gemma)     (SS/Adobe API)
```

Полная архитектура — [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## Роль в Smartology

| Агент | Роль |
|-------|------|
| Splinter | стратегия, тренды |
| Donatello | видео-контент |
| Leonardo | публикация в соцсети |
| **Leonardo** | **монетизация фотостоков** ← здесь |
| Michelangelo | личный Brain Twin (отдельный) |

Leonardo независим от остальных черепашек — отдельный поток дохода.

---

## Hard limits

- ComfyUI SDXL ~5-6 GB VRAM + Gemma ~3.5 GB > 8 GB = **OOM**. Всегда через `infra/vram_guard.py`.
- Никогда не запускать ComfyUI и Gemma одновременно.
- API ключи стоков — только `.env`, никогда в коде.
- **НЕ** публиковать контент без `review_status == approved` — ручная проверка обязательна.

---

## Структура пакета

```
Leonardo/
├── parsers/              ← TrendParser ABC + Shutterstock/Adobe/Freepik
├── prompt_engine/        ← тренд → ComfyUI workflow JSON + шаблоны
├── generation/           ← WebSocket клиент ComfyUI
├── metadata/             ← Gemma: title + keywords; rule-based: hashtags
├── uploaders/            ← StockUploader ABC + SS/Adobe реализации
├── scheduler/            ← ARQ воркер, оркестрация
├── storage/              ← asyncpg, SQLModel, миграции
├── infra/                ← config, logger, vram_guard
└── tests/
```

---

## Конвенции кода

- Python 3.12, async-first, conda env `jarvis` (или отдельный `leonardo`)
- Type hints на всех public функциях
- `ruff format` (line-length 100), `ruff check`
- Только `structlog`, никаких `print`
- `httpx.AsyncClient` вместо `requests`
- Conventional Commits: scope = модуль (`parsers`, `generation`, `metadata`, `uploaders`, `scheduler`)

---

## Зоны ответственности

### parsers/
**Можно:** добавлять новые парсеры, менять селекторы.
**Нельзя:** писать в БД напрямую — только возвращать `list[Trend]`.
**Контракт:** `TrendParser.fetch() -> list[Trend]`. Не ломать.

### prompt_engine/
**Можно:** менять шаблоны, добавлять стили, negative prompt.
**Нельзя:** HTTP-запросы, работа с БД.
**Контракт:** `build_workflow(trend, style) -> dict`. Возвращает валидный ComfyUI workflow JSON.

### generation/
**Можно:** менять polling интервал, timeout, путь сохранения.
**Нельзя:** вызывать без `vram_guard.acquire("comfyui")`.
**Контракт:** `generate(workflow: dict) -> Path`.

### metadata/
**Можно:** менять промпты, количество keywords, логику хештегов.
**Нельзя:** `tagger.py` вызывать без `vram_guard.acquire("gemma")`.
**Контракт:** `generate_metadata(trend, image_path) -> ImageMeta`.

### uploaders/
**Можно:** добавлять новые стоки — каждый отдельным файлом, реализующим `StockUploader`.
**Нельзя:** публиковать без `review_status == approved`.
**Контракт:** `StockUploader.upload(image_path, meta) -> UploadResult`.

### scheduler/pipeline.py
**Можно:** менять cron интервалы, max_jobs, retry логику.
**Нельзя:** бизнес-логику генерации/загрузки — только оркестрация.

---

## Известные проблемы — инструкции по исправлению

Каждый пункт самодостаточен. Читай свою секцию, правь только указанные файлы.

---

### FIX-RAPH-01 · asyncpg.Pool вместо connect() на каждый запрос (КРИТИЧНО)

**Проблема:** `scheduler/pipeline.py::_get_conn()` делает `asyncpg.connect()` на каждую операцию.
При 10 параллельных jobs = 10 TCP-соединений. При высокой нагрузке — исчерпание пула соединений PG.

**Файлы:** `scheduler/pipeline.py`

**Что сделать:**
1. В `WorkerSettings` добавить lifecycle hooks:
```python
class WorkerSettings:
    ...
    async def on_startup(ctx: dict) -> None:
        ctx["pool"] = await asyncpg.create_pool(
            settings.database_url, min_size=2, max_size=10
        )

    async def on_shutdown(ctx: dict) -> None:
        await ctx["pool"].close()
```
2. Удалить `_get_conn()`. Во всех функциях брать пул из ctx:
```python
async def process_job(ctx: dict, trend_id: int) -> None:
    async with ctx["pool"].acquire() as conn:
        ...
```
3. В `storage/repository.py` все функции принимают `asyncpg.Connection` — не менять сигнатуры.

---

### FIX-RAPH-02 · Реальный селектор Shutterstock (КРИТИЧНО, блокирует P0)

**Проблема:** `parsers/shutterstock.py` — селектор `[data-automation='trending-search-item']` не проверен.
С высокой вероятностью не работает на реальном сайте.

**Файлы:** `parsers/shutterstock.py`

**Что сделать:**
1. Запустить вручную с `headless=False`:
```python
browser = await p.chromium.launch(headless=False)  # временно для отладки
```
2. Перейти на `https://www.shutterstock.com/search/trending` вручную, через DevTools найти реальные классы трендовых элементов.
3. Обновить `parsers/shutterstock.py` реальными селекторами.
4. Добавить fallback — если основной селектор не нашёл элементы, попробовать `a[href*="/search/"]` внутри `.trending` контейнера.
5. Вернуть `headless=True`.
6. Добавить `page.screenshot(path="debug_ss.png")` в `except` для диагностики будущих поломок.

**Правило:** при смене UI Shutterstock — чинить только `parsers/shutterstock.py::ShutterstockParser.fetch()`. Остальной пайплайн не трогать.

---

### FIX-RAPH-03 · Реализация Shutterstock uploader (КРИТИЧНО, блокирует P0)

**Проблема:** `uploaders/base.py` — только ABC. Без реального аплоадера P0 не завершён.

**Файлы:** создать `uploaders/shutterstock.py`

**Что реализовать:**
```python
# uploaders/shutterstock.py
import httpx
from pathlib import Path
from metadata.tagger import ImageMeta
from storage.models import UploadResult
from uploaders.base import StockUploader
from infra.config import settings

class ShutterstockUploader(StockUploader):
    stock = "shutterstock"
    _base = "https://api.shutterstock.com/v2"

    async def upload(self, image_path: Path, meta: ImageMeta) -> UploadResult:
        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(headers=headers, timeout=120) as client:
            # 1. Загрузить файл
            upload_id = await self._upload_file(client, image_path)
            # 2. Отправить метаданные
            external_id = await self._submit_metadata(client, upload_id, meta)
        return UploadResult(stock=self.stock, external_id=external_id)

    async def _get_token(self) -> str:
        # OAuth2 client_credentials flow
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.shutterstock.com/v2/oauth/access_token",
                data={
                    "client_id": settings.ss_client_id,
                    "client_secret": settings.ss_client_secret,
                    "grant_type": "client_credentials",
                }
            )
            resp.raise_for_status()
            return resp.json()["access_token"]
```
Документация: `https://api-reference.shutterstock.com/#tag/Images/operation/uploadImage`

---

### FIX-RAPH-04 · SDXL модель из конфига, не хардкод

**Проблема:** `prompt_engine/builder.py::_fallback_workflow()` хардкодит `sd_xl_base_1.0.safetensors`.
Может не совпадать с тем что реально установлено в ComfyUI.

**Файлы:** `infra/config.py`, `prompt_engine/builder.py`

**Что сделать:**
1. Добавить в `infra/config.py`:
```python
comfyui_model: str = "sd_xl_base_1.0.safetensors"
```
2. В `prompt_engine/builder.py` заменить хардкод:
```python
from infra.config import settings
# ...
"4": {"class_type": "CheckpointLoaderSimple",
      "inputs": {"ckpt_name": settings.comfyui_model}},
```
3. В `.env.example` добавить `COMFYUI_MODEL=sd_xl_base_1.0.safetensors`.

---

### FIX-RAPH-05 · Очистка output/ директории

**Проблема:** PNG файлы накапливаются бесконечно. При 60-80 фото/день (~3-5 MB каждый) — диск забьётся за 2 недели.

**Файлы:** `scheduler/pipeline.py`

**Что сделать — добавить ARQ cron задачу:**
```python
async def cleanup_output(ctx: dict) -> None:
    """Delete output files older than KEEP_DAYS days."""
    output_dir = settings.comfyui_output_dir
    cutoff = datetime.utcnow() - timedelta(days=settings.output_keep_days)
    deleted = 0
    for f in output_dir.glob("*.png"):
        if datetime.utcfromtimestamp(f.stat().st_mtime) < cutoff:
            f.unlink()
            deleted += 1
    log.info("cleanup.done", deleted=deleted)

class WorkerSettings:
    functions = [parse_trends, process_job, enqueue_pending, cleanup_output]
    cron_jobs = [
        ...
        cron(cleanup_output, hour={3}),   # каждую ночь в 03:00
    ]
```
Добавить в `infra/config.py`: `output_keep_days: int = 14`.

---

### FIX-RAPH-06 · Retry на парсеры и генерацию

**Проблема:** нет повторных попыток при сетевых ошибках парсеров и таймаутах ComfyUI.

**Файлы:** создать `infra/retry.py`, применить в `parsers/`, `generation/comfyui_client.py`

```python
# infra/retry.py
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import httpx
from playwright.async_api import Error as PlaywrightError

parser_retry = retry(
    retry=retry_if_exception_type((Exception,)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=5, max=30),
    reraise=True,
)

comfyui_retry = retry(
    retry=retry_if_exception_type((httpx.HTTPError, TimeoutError)),
    stop=stop_after_attempt(2),
    wait=wait_exponential(min=10, max=60),
    reraise=True,
)
```
Применить `@parser_retry` на `TrendParser.fetch()`, `@comfyui_retry` на `generate()`.

---

## Запуск

```bash
# Создать БД
createdb -U creator leonardo && psql -U creator leonardo < storage/migrations/001_init.sql

# Разовый прогон
cd ~/projects/Leonardo && source .venv/bin/activate
python -m scheduler.pipeline --run-once

# ARQ воркер
arq scheduler.pipeline.WorkerSettings

# Тесты
pytest tests/ -v
```

---

## Связанные файлы

- [`~/projects/AGENTS.md`](../AGENTS.md) — workspace-уровень
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — архитектурный контракт
- `.env.example` — список переменных
