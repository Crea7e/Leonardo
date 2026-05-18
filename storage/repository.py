import asyncpg

from parsers.base import Trend as ParsedTrend
from storage.models import UploadResult


async def save_trends(conn: asyncpg.Connection, trends: list[ParsedTrend]) -> int:
    """Insert trends, skip duplicates (source+keyword+date). Returns inserted count."""
    rows = [(t.source, t.keyword, t.score, t.captured_at) for t in trends]
    result = await conn.executemany(
        """
        INSERT INTO trends (source, keyword, score, captured_at)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (source, keyword, (captured_at::date)) DO NOTHING
        """,
        rows,
    )
    return int(result.split()[-1]) if result else 0


async def get_unprocessed_trends(conn: asyncpg.Connection, limit: int = 10) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT * FROM trends WHERE is_processed = FALSE ORDER BY score DESC LIMIT $1",
        limit,
    )


async def create_job(conn: asyncpg.Connection, trend_id: int) -> int:
    row = await conn.fetchrow(
        "INSERT INTO jobs (trend_id) VALUES ($1) RETURNING id",
        trend_id,
    )
    return row["id"]


async def update_job(conn: asyncpg.Connection, job_id: int, **fields) -> None:
    if not fields:
        return
    assignments = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(fields))
    values = list(fields.values())
    await conn.execute(
        f"UPDATE jobs SET {assignments}, updated_at = NOW() WHERE id = $1",
        job_id,
        *values,
    )


async def save_upload_result(conn: asyncpg.Connection, result: UploadResult) -> None:
    await conn.execute(
        """
        INSERT INTO upload_results (job_id, stock, external_id, review_status)
        VALUES ($1, $2, $3, $4)
        """,
        result.job_id,
        result.stock,
        result.external_id,
        result.review_status,
    )


async def mark_trend_processed(conn: asyncpg.Connection, trend_id: int) -> None:
    await conn.execute(
        "UPDATE trends SET is_processed = TRUE WHERE id = $1",
        trend_id,
    )
