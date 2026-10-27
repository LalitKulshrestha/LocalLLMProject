"""Platform formats for generation workflows.

These are model-friendly render sizes, not a claim that an installed model can
produce a particular delivery resolution. Hosted providers can use the ratio
directly when one is configured later.
"""

VIDEO_FORMATS = {
    "shorts": {"ratio": "9:16", "width": 576, "height": 1024},
    "youtube": {"ratio": "16:9", "width": 1024, "height": 576},
}


def generation_size(kind: str, output_format: str) -> tuple[int, int, str]:
    if kind == "text_to_image":
        return 512, 512, "1:1"
    if output_format not in VIDEO_FORMATS:
        raise ValueError("Choose Reels / Shorts or YouTube landscape")
    preset = VIDEO_FORMATS[output_format]
    return preset["width"], preset["height"], preset["ratio"]
