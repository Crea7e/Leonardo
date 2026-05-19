"""
ARQ worker — orchestrates the full pipeline.

Run:
    arq scheduler.pipeline.WorkerSettings

One-shot:
    python -m scheduler.pipeline --run-once
"""

import asyncio
from pathlib import Path

import asyncpg
from arq import create_pool, cron
from arq.connections import RedisSettings

from infra.config import settings
from infra.logger import log
from metadata.hashtags import build_hashtags
from metadata.tagger import generate_metadata
from parsers.base import Trend
from parsers.shutterstock import ShutterstockParser
from storage import repository


async def _get_conn() -> asyncpg.Connection:
    return await asyncpg.connect(settings.database_url)


async def parse_trends(ctx: dict) -> None:
    """Fetch trending keywords from all sources and persist to DB."""
    parsers = [ShutterstockParser()]
    conn = await _get_conn()
    try:
        for parser in parsers:
            try:
                trends = await parser.fetch()
                count = await repository.save_trends(conn, trends)
                log.info("trends.saved", source=parser.source, count=count)
            except Exception:
                log.exception("trends.parse_failed", source=parser.source)
    finally:
        await conn.close()


async def process_job(ctx: dict, trend_id: int) -> None:
    """Full pipeline for one trend: generate → metadata → upload."""
    conn = await _get_conn()
    job_id: int | None = None

    try:
        job_id = await repository.create_job(conn, trend_id)
        trend_row = await repository.get_trend_by_id(conn, trend_id)
        if trend_row is None:
            raise ValueError(f"Trend {trend_id} not found")

        trend = Trend(
            keyword=trend_row["keyword"],
            source=trend_row["source"],
            score=trend_row["score"] or 1.0,
        )

        await repository.update_job(conn, job_id, status="generating")
        prompt = build_prompt(trend)
        image_path: Path = await imagen_client.generate(
            prompt, aspect_ratio=settings.imagen_aspect_ratio
        )
        await repository.update_job(conn, job_id, status="uploading", image_path=str(image_path))

        meta = await generate_metadata(trend, image_path)
        hashtags = build_hashtags(trend, meta.keywords)

        await repository.update_job(
            conn,
            job_id,
            status="uploading",
            title=meta.title,
            keywords=meta.keywords,
            hashtags=hashtags,
            category=meta.category,
        )

        # TODO P1: iterate over enabled uploaders
        # result = await ShutterstockUploader().upload(image_path, meta)
        # await repository.save_upload_result(conn, result)

        await repository.update_job(conn, job_id, status="done")
        await repository.mark_trend_processed(conn, trend_id)
        log.info("pipeline.done", job_id=job_id, keyword=trend.keyword)

    except Exception as exc:
        log.exception("pipeline.failed", job_id=job_id, trend_id=trend_id)
        if job_id is not None:
            await repository.update_job(conn, job_id, status="failed", error_msg=str(exc))
    finally:
        await conn.close()


async def enqueue_pending(ctx: dict) -> None:
    """Pick unprocessed trends and push them into the ARQ queue."""
    conn = await _get_conn()
    try:
        rows = await repository.get_unprocessed_trends(conn, limit=settings.max_jobs_per_run)
        for row in rows:
            await ctx["redis"].enqueue_job("process_job", row["id"])
            log.info("pipeline.enqueued", trend_id=row["id"], keyword=row["keyword"])
    finally:
        await conn.close()


class WorkerSettings:
    functions = [parse_trends, process_job, enqueue_pending]
    cron_jobs = [
        cron(parse_trends, hour={0, 6, 12, 18}),  # every 6 hours
        cron(enqueue_pending, minute={0, 15, 30, 45}),
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 3  # limit concurrency — VRAM guard handles serialization


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--run-once", action="store_true")
    args = parser.parse_args()

    if args.run_once:

        async def _run_once() -> None:
            await parse_trends({})
            redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
            try:
                await enqueue_pending({"redis": redis})
            finally:
                await redis.close()

        asyncio.run(_run_once())
