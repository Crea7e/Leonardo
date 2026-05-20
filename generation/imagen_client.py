"""
Google AI Studio — Gemini image generation.

Default model: gemini-2.5-flash-image  (бесплатный тир)
Paid alt:      imagen-4.0-fast-generate-001 ($0.02/image, нужен billing)
Override via:  IMAGEN_MODEL= in .env

Docs:       https://ai.google.dev/gemini-api/docs/image-generation
Free tier:  15 RPM — при 429 подождать 60 сек
Quota page: https://aistudio.google.com/rate-limit
"""

import base64
import uuid
from pathlib import Path

import httpx

from infra.config import settings
from infra.logger import log

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


async def generate(prompt: str, aspect_ratio: str = "square") -> Path:
    """Generate image via Gemini image model, return Path to saved PNG."""
    output_dir = settings.imagen_output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    model = settings.imagen_model
    url = f"{_BASE_URL}/{model}:generateContent?key={settings.google_api_key}"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }

    async with httpx.AsyncClient(timeout=120) as client:
        log.info("imagen.generating", model=model, prompt=prompt[:60])
        resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"Imagen API {resp.status_code}: {resp.text}")

    data = resp.json()
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    for part in parts:
        if "inlineData" in part:
            image_bytes = base64.b64decode(part["inlineData"]["data"])
            dest = output_dir / f"raphael_{uuid.uuid4().hex[:8]}.png"
            dest.write_bytes(image_bytes)
            log.info("imagen.saved", path=str(dest), size_kb=len(image_bytes) // 1024)
            return dest

    raise RuntimeError(f"No image in response: {data}")
