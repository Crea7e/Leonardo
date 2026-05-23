"""
Shutterstock Contributor API uploader.

Docs: https://api-reference.shutterstock.com/

Auth: Basic Auth — base64(client_id:client_secret)

Upload flow:
  1. POST /v2/images — multipart: image file + metadata fields
  2. Response: {"id": "...", "status": "pending_review", ...}
  3. external_id = response["id"]

Category IDs (Shutterstock): https://api-reference.shutterstock.com/#tag/Images/operation/uploadImage
"""

import base64
import mimetypes
from pathlib import Path

import httpx

from infra.config import settings
from infra.logger import log
from metadata.tagger import ImageMeta
from storage.models import UploadResult
from uploaders.base import StockUploader

_BASE = "https://api.shutterstock.com/v2"
_TIMEOUT = 120

# Shutterstock category IDs — https://www.shutterstock.com/contributorsupport/articles/
_CATEGORY_MAP: dict[str, int] = {
    "Nature": 15,
    "Business": 6,
    "Technology": 23,
    "People": 18,
    "Travel": 17,
    "Food": 9,
    "Architecture": 5,
}


class ShutterstockUploader(StockUploader):
    stock = "shutterstock"

    def _auth_header(self) -> str:
        raw = f"{settings.ss_client_id}:{settings.ss_client_secret}"
        return "Basic " + base64.b64encode(raw.encode()).decode()

    async def upload(self, job_id: int, image_path: Path, meta: ImageMeta) -> UploadResult:
        category_id = _CATEGORY_MAP.get(meta.category, 15)
        keywords_str = ",".join(meta.keywords[:50])
        mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"

        headers = {
            "Authorization": self._auth_header(),
            "Accept": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                with image_path.open("rb") as fh:
                    files = {"filename": (image_path.name, fh, mime)}
                    data = {
                        "title": meta.title[:200],
                        "description": meta.title[:1000],
                        "keywords": keywords_str,
                        "categories": str(category_id),
                        "model_releases": "0",
                        "editorial": "false",
                        "content_available": "true",
                    }
                    log.info(
                        "ss.uploading",
                        job_id=job_id,
                        title=meta.title[:60],
                        keywords=len(meta.keywords),
                    )
                    resp = await client.post(
                        f"{_BASE}/images",
                        headers=headers,
                        files=files,
                        data=data,
                    )

            if resp.status_code in (200, 201):
                body = resp.json()
                external_id = str(body.get("id", ""))
                log.info("ss.uploaded", job_id=job_id, external_id=external_id)
                return UploadResult(
                    job_id=job_id,
                    stock=self.stock,
                    external_id=external_id,
                    review_status="pending",
                )

            log.error(
                "ss.upload_failed",
                job_id=job_id,
                status=resp.status_code,
                body=resp.text[:300],
            )
            return UploadResult(
                job_id=job_id,
                stock=self.stock,
                external_id=None,
                review_status="failed",
            )

        except Exception:
            log.exception("ss.upload_error", job_id=job_id)
            return UploadResult(
                job_id=job_id,
                stock=self.stock,
                external_id=None,
                review_status="failed",
            )
