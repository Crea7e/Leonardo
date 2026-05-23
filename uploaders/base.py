from abc import ABC, abstractmethod
from pathlib import Path

from metadata.tagger import ImageMeta
from storage.models import UploadResult


class StockUploader(ABC):
    stock: str = ""

    @abstractmethod
    async def upload(self, job_id: int, image_path: Path, meta: ImageMeta) -> UploadResult:
        """Upload image with metadata. Returns UploadResult ready to persist."""
        ...
