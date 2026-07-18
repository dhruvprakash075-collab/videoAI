"""Deterministic manga panel page compositor."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps


def _fixed_rects(count: int, width: int, height: int, margin: int, gutter: int) -> list[tuple[int, int, int, int]]:
    count = max(1, min(5, count))
    left, top, right, bottom = margin, margin, width - margin, height - margin
    mid_x = (left + right) // 2
    mid_y = (top + bottom) // 2
    if count == 1:
        return [(left, top, right, bottom)]
    if count == 2:
        return [(left, top, mid_x - gutter // 2, bottom), (mid_x + gutter // 2, top, right, bottom)]
    if count == 3:
        return [
            (left, top, mid_x - gutter // 2, mid_y - gutter // 2),
            (mid_x + gutter // 2, top, right, mid_y - gutter // 2),
            (left, mid_y + gutter // 2, right, bottom),
        ]
    if count == 4:
        return [
            (left, top, mid_x - gutter // 2, mid_y - gutter // 2),
            (mid_x + gutter // 2, top, right, mid_y - gutter // 2),
            (left, mid_y + gutter // 2, mid_x - gutter // 2, bottom),
            (mid_x + gutter // 2, mid_y + gutter // 2, right, bottom),
        ]
    band1 = top + (bottom - top) * 36 // 100
    band2 = top + (bottom - top) * 70 // 100
    return [
        (left, top, mid_x - gutter // 2, band1 - gutter // 2),
        (mid_x + gutter // 2, top, right, band1 - gutter // 2),
        (left, band1 + gutter // 2, right, band2 - gutter // 2),
        (left, band2 + gutter // 2, mid_x - gutter // 2, bottom),
        (mid_x + gutter // 2, band2 + gutter // 2, right, bottom),
    ]


def _layout_rects(layout_file: Path | None, count: int, width: int, height: int, page_index: int) -> list[tuple[int, int, int, int]]:
    if not layout_file or not layout_file.is_file():
        return []
    layouts = json.loads(layout_file.read_text(encoding="utf-8"))
    matches = [item for item in layouts if len(item.get("panels", [])) == count]
    if not matches:
        return []
    candidates = []
    for offset in range(len(matches)):
        panels = matches[(page_index + offset) % len(matches)]["panels"]
        rects = [
            (
                int(x1 * width),
                int(y1 * height),
                int(x2 * width),
                int(y2 * height),
            )
            for x1, y1, x2, y2 in panels
        ]
        if _valid_rects(rects, width, height):
            candidates.append(rects)
    if not candidates:
        return []
    return min(candidates, key=_layout_aspect_score)


def _layout_aspect_score(rects: list[tuple[int, int, int, int]]) -> float:
    # ComfyUI's manga workflow creates 768x512 landscape shots (3:2).
    return sum(abs(math.log(((x2 - x1) / (y2 - y1)) / 1.5)) for x1, y1, x2, y2 in rects)


def _valid_rects(rects: list[tuple[int, int, int, int]], width: int, height: int) -> bool:
    page_area = width * height
    for x1, y1, x2, y2 in rects:
        if x2 <= x1 or y2 <= y1:
            return False
        if (x2 - x1) * (y2 - y1) < page_area * 0.03:
            return False
    for i, a in enumerate(rects):
        ax1, ay1, ax2, ay2 = a
        area_a = (ax2 - ax1) * (ay2 - ay1)
        for bx1, by1, bx2, by2 in rects[i + 1:]:
            ix = max(0, min(ax2, bx2) - max(ax1, bx1))
            iy = max(0, min(ay2, by2) - max(ay1, by1))
            if ix * iy > area_a * 0.02:
                return False
    return True


def _panel_image(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    source = img.convert("RGB")
    background = ImageOps.fit(
        source,
        size,
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )
    background = ImageEnhance.Brightness(background.filter(ImageFilter.GaussianBlur(12))).enhance(0.82)
    foreground = ImageOps.contain(source, size, method=Image.Resampling.LANCZOS)
    background.paste(foreground, ((size[0] - foreground.width) // 2, (size[1] - foreground.height) // 2))
    return background


def compose_panel_pages(
    image_paths: Iterable[Path],
    output_dir: Path,
    *,
    width: int = 1920,
    height: int = 1080,
    margin: int = 48,
    gutter: int = 24,
    border: int = 6,
    prefix: str = "manga_page",
    layout_file: Path | None = None,
    fallback_layout_file: Path | None = None,
) -> list[Path]:
    """Paste distinct images into fixed manga panels and draw borders on top."""
    paths = [Path(p) for p in image_paths]
    if not paths:
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    pages: list[Path] = []
    for page_i in range(0, len(paths), 5):
        chunk = paths[page_i : page_i + 5]
        canvas = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(canvas)
        rects = _layout_rects(layout_file, len(chunk), width, height, len(pages))
        if not rects:
            rects = _layout_rects(fallback_layout_file, len(chunk), width, height, len(pages))
        if not rects:
            rects = _fixed_rects(len(chunk), width, height, margin, gutter)
        for path, rect in zip(chunk, rects, strict=True):
            x1, y1, x2, y2 = rect
            with Image.open(path) as img:
                fitted = _panel_image(img, (x2 - x1, y2 - y1))
            canvas.paste(fitted, (x1, y1))
        for rect in rects:
            draw.rectangle(rect, outline="black", width=border)
        out = output_dir / f"{prefix}_{len(pages) + 1:02d}.png"
        canvas.save(out)
        pages.append(out)
    return pages
