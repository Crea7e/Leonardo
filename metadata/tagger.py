from dataclasses import dataclass
from pathlib import Path

import httpx

from infra import vram_guard
from infra.config import settings
from infra.logger import log
from parsers.base import Trend

SYSTEM_PROMPT = """You are a professional photo stock metadata expert.
Given a trend keyword, generate metadata for a stock photo.
Respond ONLY with valid JSON, no markdown.

Format:
{
  "title": "...",         // max 200 chars, descriptive
  "keywords": ["..."],    // exactly 50 relevant keywords, single words or short phrases
  "category": "..."       // one of: Nature, Business, Technology, People, Travel, Food, Architecture
}"""


@dataclass
class ImageMeta:
    title: str
    keywords: list[str]
    category: str


async def generate_metadata(trend: Trend, image_path: Path) -> ImageMeta:
    """Call Gemma via Ollama to produce title, 50 keywords, and category."""
    prompt = f'Trend keyword: "{trend.keyword}" (source: {trend.source})'

    async with vram_guard.acquire("gemma"):
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{settings.ollama_url}/api/chat",
                json={
                    "model": settings.ollama_model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "format": "json",
                },
            )
            resp.raise_for_status()

    data = resp.json()["message"]["content"]
    import json

    parsed = json.loads(data)
    keywords = parsed.get("keywords", [])[:50]
    meta = ImageMeta(
        title=parsed.get("title", trend.keyword)[:200],
        keywords=keywords,
        category=parsed.get("category", "Nature"),
    )
    log.info("metadata.generated", keyword=trend.keyword, keywords_count=len(keywords))
    return meta
