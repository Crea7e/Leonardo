"""
Single asyncio.Lock for RTX 5060 8GB VRAM.
ComfyUI (~5-6 GB) and Gemma (~3.5 GB) are mutually exclusive.

Usage:
    async with vram_guard.acquire("comfyui"):
        ...
    async with vram_guard.acquire("gemma"):
        ...
"""

import asyncio
from contextlib import asynccontextmanager

import structlog

log = structlog.get_logger()

_lock = asyncio.Lock()
_current_holder: str | None = None


@asynccontextmanager
async def acquire(component: str):
    global _current_holder
    log.info("vram.waiting", component=component)
    async with _lock:
        _current_holder = component
        log.info("vram.acquired", component=component)
        try:
            yield
        finally:
            log.info("vram.released", component=component)
            _current_holder = None
