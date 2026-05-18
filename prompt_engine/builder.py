import json
from pathlib import Path

from parsers.base import Trend

TEMPLATES_DIR = Path(__file__).parent / "templates"

NEGATIVE_PROMPT = (
    "blurry, low quality, watermark, text, logo, signature, "
    "duplicate, deformed, ugly, bad anatomy, jpeg artifacts"
)

STYLE_MODIFIERS = {
    "photorealistic": "professional stock photo, sharp focus, natural lighting, 4k",
    "lifestyle": "candid lifestyle photography, warm tones, natural light",
    "corporate": "corporate photography, clean background, professional",
}


def build_workflow(trend: Trend, style: str = "photorealistic") -> dict:
    """Load SDXL base template and inject trend-specific prompts."""
    template_path = TEMPLATES_DIR / "sdxl_base.json"
    if not template_path.exists():
        return _fallback_workflow(trend, style)

    with template_path.open() as f:
        workflow = json.load(f)

    positive = f"{trend.keyword}, {STYLE_MODIFIERS.get(style, STYLE_MODIFIERS['photorealistic'])}"

    # Node IDs follow ComfyUI SDXL base workflow convention
    for node in workflow.values():
        if isinstance(node, dict) and node.get("class_type") == "CLIPTextEncode":
            inputs = node.get("inputs", {})
            if "positive" in str(inputs.get("_meta", "")):
                inputs["text"] = positive
            elif "negative" in str(inputs.get("_meta", "")):
                inputs["text"] = NEGATIVE_PROMPT

    return workflow


def _fallback_workflow(trend: Trend, style: str) -> dict:
    """Minimal valid ComfyUI workflow when no template file exists."""
    positive = f"{trend.keyword}, {STYLE_MODIFIERS.get(style, STYLE_MODIFIERS['photorealistic'])}"
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 0,
                "steps": 30,
                "cfg": 7.0,
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
        },
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["4", 1]}},
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": NEGATIVE_PROMPT, "clip": ["4", 1]},
        },
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "raphael", "images": ["8", 0]},
        },
    }
