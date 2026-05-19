from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class Trend:
    keyword: str
    source: str  # shutterstock|adobe|freepik
    score: float = 1.0
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class TrendParser(ABC):
    source: str = ""

    @abstractmethod
    async def fetch(self) -> list[Trend]:
        """Parses trending keywords from the stock. Returns list of Trend objects."""
        ...
