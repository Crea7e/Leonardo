"""
Hugging Face Inference API — generation + upscale pipeline.

Step 1 — Generate: FLUX.1-schnell → 1024×1024 PNG (free, ~1000 req/day)
Step 2 — Upscale:  Real-ESRGAN 4x  → 4096×4096 PNG (free)

HF token: https://huggingface.co/settings/tokens (read-only достаточно)
Free tier docs: https://huggingface.co/docs/api-inference/pricing
"""

import uuid
from pathlib import Path

import httpx

from infra.config import settings
from infra.logger import log

_HF_BASE = "https://api-inference.huggingface.co/models"
_GENERATE_MODEL = "black-forest-labs/FLUX.1-schnell"
_UPSCALE_MODEL = "caidas/swin2SR-realworld-sr-x4-64-bsrgan-psnr"
_TIMEOUT = 120


async def generate(prompt: str, aspect_ratio: str = "square") -> Path:
    """Generate image with FLUX.1-schnell, upscale 4x, return Path to PNG."""
    output_dir = settings.imagen_output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_bytes = await _generate_flux(prompt)

    tmp = output_dir / f"_tmp_{uuid.uuid4().hex[:8]}.png"
    tmp.write_bytes(raw_bytes)

    try:
        upscaled = await _upscale(raw_bytes, output_dir)
        tmp.unlink(missing_ok=True)
        return upscaled
    except Exception as e:
        log.warning("upscale.failed", reason=str(e), fallback="using original")
        dest = output_dir / f"raphael_{uuid.uuid4().hex[:8]}.png"
        tmp.rename(dest)
        return dest


async def _generate_flux(prompt: str) -> bytes:
    url = f"{_HF_BASE}/{_GENERATE_MODEL}"
    headers = {"Authorization": f"Bearer {settings.hf_token}"}
    payload = {
        "inputs": prompt,
        "parameters": {"num_inference_steps": 4, "guidance_scale": 0.0},
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        log.info("flux.generating", model=_GENERATE_MODEL, prompt=prompt[:60])
        resp = await client.post(url, json=payload, headers=headers)

        # модель может грузиться — ждём и повторяем
        if resp.status_code == 503:
            import asyncio

            wait = resp.json().get("estimated_time", 20)
            log.info("flux.model_loading", wait_sec=wait)
            await asyncio.sleep(min(wait, 30))
            resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code != 200:
            raise RuntimeError(f"FLUX API {resp.status_code}: {resp.text[:300]}")

    log.info("flux.done", size_kb=len(resp.content) // 1024)
    return resp.content


async def _upscale(image_bytes: bytes, output_dir: Path) -> Path:
    url = f"{_HF_BASE}/{_UPSCALE_MODEL}"
    headers = {
        "Authorization": f"Bearer {settings.hf_token}",
        "Content-Type": "image/png",
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        log.info("upscale.start", model=_UPSCALE_MODEL)
        resp = await client.post(url, content=image_bytes, headers=headers)

        if resp.status_code == 503:
            import asyncio

            wait = resp.json().get("estimated_time", 20)
            log.info("upscale.model_loading", wait_sec=wait)
            await asyncio.sleep(min(wait, 30))
            resp = await client.post(url, content=image_bytes, headers=headers)

        if resp.status_code != 200:
            raise RuntimeError(f"Upscale API {resp.status_code}: {resp.text[:300]}")

    dest = output_dir / f"raphael_{uuid.uuid4().hex[:8]}_4x.png"
    dest.write_bytes(resp.content)
    log.info("upscale.done", path=str(dest), size_kb=len(resp.content) // 1024)
    return dest
