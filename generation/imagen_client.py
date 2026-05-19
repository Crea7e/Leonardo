"""
Google AI Studio — Imagen image generation.

Default model: imagen-3.0-fast-generate-001  (Imagen 3 Fast, "нано банана 2")
Newer equiv:   imagen-4.0-fast-generate-001  (Imagen 4 Fast)
Override via:  IMAGEN_MODEL= in .env

Docs:       https://ai.google.dev/api/generate-images
Free tier:  ~500-1000 images/day, 15 RPM
Paid:       $0.02/image (Fast), $0.04/image (Standard)
Quota page: https://aistudio.google.com/rate-limit
"""

import base64
import uuid
from pathlib import Path

import httpx

from infra.config import settings
from infra.logger import log

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

ASPECT_RATIOS = {
    "square": "1:1",  # 1024×1024 — стандарт стоков
    "landscape": "4:3",  # 1024×768
    "portrait": "3:4",  # 768×1024
    "wide": "16:9",
}


async def generate(prompt: str, aspect_ratio: str = "square") -> Path:
    """Generate image via Imagen, return Path to saved PNG."""
    output_dir = settings.imagen_output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    model = settings.imagen_model
    ratio = ASPECT_RATIOS.get(aspect_ratio, ASPECT_RATIOS[settings.imagen_aspect_ratio])
    payload = {
        "instances": [{"prompt": prompt}],
        "parameters": {
            "sampleCount": 1,
            "aspectRatio": ratio,
            "safetyFilterLevel": "block_some",
            "personGeneration": "allow_all",
        },
    }

    url = f"{_BASE_URL}/{model}:predict?key={settings.google_api_key}"

    async with httpx.AsyncClient(timeout=60) as client:
        log.info("imagen.generating", model=model, ratio=ratio, prompt=prompt[:60])
        resp = await client.post(url, json=payload)
        resp.raise_for_status()

    data = resp.json()
    predictions = data.get("predictions", [])
    if not predictions:
        raise RuntimeError(f"Imagen returned no predictions: {data}")

    b64 = predictions[0]["bytesBase64Encoded"]
    image_bytes = base64.b64decode(b64)

    dest = output_dir / f"raphael_{uuid.uuid4().hex[:8]}.png"
    dest.write_bytes(image_bytes)
    log.info("imagen.saved", path=str(dest), size_kb=len(image_bytes) // 1024)
    return dest
