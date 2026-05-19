from parsers.base import Trend

STYLE_MODIFIERS = {
    "photorealistic": "professional stock photo, sharp focus, natural lighting, 4k, high resolution",
    "lifestyle": "candid lifestyle photography, warm tones, natural light, authentic",
    "corporate": "corporate photography, clean white background, professional, studio lighting",
    "nature": "nature photography, golden hour, vivid colors, wide angle",
    "food": "food photography, appetizing, shallow depth of field, restaurant quality",
}

_QUALITY_SUFFIX = (
    "no text, no watermark, no logo, no signature, commercial stock photography, high quality"
)


def build_prompt(trend: Trend, style: str = "photorealistic") -> str:
    """Build Imagen text prompt from trend keyword and style."""
    modifier = STYLE_MODIFIERS.get(style, STYLE_MODIFIERS["photorealistic"])
    return f"{trend.keyword}, {modifier}, {_QUALITY_SUFFIX}"
