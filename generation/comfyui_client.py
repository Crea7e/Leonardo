import asyncio
import json
import uuid
from pathlib import Path

import httpx
import websockets
from infra.logger import log

from infra import vram_guard
from infra.config import settings


async def generate(workflow: dict) -> Path:
    """Send workflow to ComfyUI, wait for completion, return Path to PNG."""
    async with vram_guard.acquire("comfyui"):
        client_id = str(uuid.uuid4())
        ws_url = f"{settings.comfyui_url}/ws?client_id={client_id}"
        http_base = settings.comfyui_url.replace("ws://", "http://").replace("wss://", "https://")

        async with websockets.connect(ws_url) as ws:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{http_base}/prompt",
                    json={"prompt": workflow, "client_id": client_id},
                )
                resp.raise_for_status()
                prompt_id = resp.json()["prompt_id"]

            log.info("comfyui.generating", prompt_id=prompt_id)
            prompt_id = await _wait_for_completion(ws, prompt_id)

        return await _download_result(http_base, prompt_id)


async def _wait_for_completion(ws, prompt_id: str) -> str:
    async for raw in ws:
        msg = json.loads(raw)
        if msg.get("type") == "executing" and msg["data"].get("node") is None:
            if msg["data"].get("prompt_id") == prompt_id:
                return prompt_id
    return prompt_id


async def _download_result(http_base: str, prompt_id: str) -> Path:
    output_dir = settings.comfyui_output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient() as client:
        for _ in range(30):
            resp = await client.get(f"{http_base}/history/{prompt_id}")
            resp.raise_for_status()
            history = resp.json()
            if prompt_id in history:
                outputs = history[prompt_id]["outputs"]
                for node_output in outputs.values():
                    for img in node_output.get("images", []):
                        img_resp = await client.get(
                            f"{http_base}/view",
                            params={"filename": img["filename"], "subfolder": img["subfolder"]},
                        )
                        img_resp.raise_for_status()
                        dest = output_dir / img["filename"]
                        dest.write_bytes(img_resp.content)
                        log.info("comfyui.saved", path=str(dest))
                        return dest
            await asyncio.sleep(2)

    raise TimeoutError(f"ComfyUI did not produce output for prompt_id={prompt_id}")
