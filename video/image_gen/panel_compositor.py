"""Deterministic manga panel page compositor."""

from __future__ import annotations

import json
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
    if not layout_file:
        return []
    layout_file = Path(layout_file)
    if not layout_file.is_file():
        return []
    layouts = json.loads(layout_file.read_text(encoding="utf-8"))
    matches = [item for item in layouts if len(item.get("panels", [])) == count]
    if not matches:
        return []
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
            return rects
    return []


def _valid_rects(rects: list[tuple[int, int, int, int]], width: int, height: int) -> bool:
    page_area = width * height
    for x1, y1, x2, y2 in rects:
        if x2 <= x1 or y2 <= y1:
            return False
        # ponytail: lowered from 3% to 1.5% — Roboflow manga-panel annotations
        # include small inset panels (e.g. close-up inserts) that are valid manga
        # layout elements, not noise. Raise back to 3% if dataset quality drops.
        if (x2 - x1) * (y2 - y1) < page_area * 0.015:
            return False
    for i, a in enumerate(rects):
        ax1, ay1, ax2, ay2 = a
        area_a = (ax2 - ax1) * (ay2 - ay1)
        for bx1, by1, bx2, by2 in rects[i + 1:]:
            ix = max(0, min(ax2, bx2) - max(ax1, bx1))
            iy = max(0, min(ay2, by2) - max(ay1, by1))
            # ponytail: raised from 2% to 5% — Roboflow annotations often have
            # natural border overlap (panel borders drawn fractionally inside
            # adjacent panels). 5% tolerates this while still rejecting truly
            # overlapping layouts.
            if ix * iy > area_a * 0.05:
                return False
    return True


def _panel_image(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Crop-fill the image into the panel rect — no letterbox, no blurred fill.

    Panel rect aspects (0.5-7.6 in the roboflow dataset) can't be matched by a
    small bucket set, so contain+blur-fill showed bars everywhere. Center-crop
    always covers the rect exactly; extreme panels just show a tighter crop.
    """
    return ImageOps.fit(
        img.convert("RGB"),
        size,
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )


def page_canvas_size(width: int, height: int, margin: int, page_aspect: float) -> tuple[int, int]:
    """Frame-relative manga page size; ``page_aspect <= 0`` = full-bleed frame."""
    if page_aspect <= 0:
        return width, height
    page_h = max(1, height - 2 * margin)
    page_w = max(1, round(page_h / page_aspect))
    return page_w, page_h


def plan_page_rects(
    count: int,
    width: int,
    height: int,
    page_index: int,
    *,
    layout_file: Path | None = None,
    fallback_layout_file: Path | None = None,
    margin: int = 48,
    gutter: int = 24,
) -> list[tuple[int, int, int, int]]:
    """Resolve one page's panel rects: dataset layout → fallback → fixed grid.

    Shared by compose_panel_pages (drawing) and image_gen (per-panel
    generation sizes) so both always agree on the geometry.
    """
    rects = _layout_rects(layout_file, count, width, height, page_index)
    if not rects:
        rects = _layout_rects(fallback_layout_file, count, width, height, page_index)
    if not rects:
        rects = _fixed_rects(count, width, height, margin, gutter)
    return rects


def _read_layout_counts(layout_file: Path | None) -> list[int] | None:
    """Panel count per dataset layout, or None when unreadable/absent."""
    if not layout_file:
        return None
    layout_file = Path(layout_file)
    if not layout_file.is_file():
        return None
    try:
        layouts = json.loads(layout_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    counts = [len(item.get("panels", [])) for item in layouts if item.get("panels")]
    return counts or None


def plan_page_counts(
    n_images: int,
    layout_file: Path | None = None,
    fallback_layout_file: Path | None = None,
    default_panels: int = 5,
) -> list[int]:
    """Panel count per page, mirroring plan_page_rects' dataset chain.

    Pages walk the layout dataset in order (each page takes the next
    layout's panel count, cycling); the final page takes the first count
    <= remaining, else the remainder itself (the fixed grid covers it).
    Without a dataset the legacy uniform ``default_panels`` grid applies.
    """
    seq = _read_layout_counts(layout_file) or _read_layout_counts(fallback_layout_file)
    if not seq:
        seq = [default_panels]
    counts: list[int] = []
    remaining = n_images
    i = 0
    while remaining > 0:
        c = seq[i % len(seq)]
        if c > remaining:
            for j in range(len(seq)):
                c2 = seq[(i + j) % len(seq)]
                if c2 <= remaining:
                    c = c2
                    break
            else:
                c = remaining
        counts.append(c)
        remaining -= c
        i += 1
    return counts


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
    page_aspect: float = 1.414,
    page_blur: bool = True,
) -> list[Path]:
    """Paste distinct images into fixed manga panels and draw borders on top.

    The page is composed at ``page_aspect`` (A4 portrait by default) and
    centered on the full-frame canvas; the leftover screen is filled with a
    blurred zoomed copy of the page. ``page_aspect <= 0`` restores the legacy
    full-bleed frame.
    """
    paths = [Path(p) for p in image_paths]
    if not paths:
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    pages: list[Path] = []
    page_w, page_h = page_canvas_size(width, height, margin, page_aspect)
    full_bleed = (page_w, page_h) == (width, height)
    offset = 0
    for page_count in plan_page_counts(len(paths), layout_file, fallback_layout_file):
        chunk = paths[offset : offset + page_count]
        offset += page_count
        page = Image.new("RGB", (page_w, page_h), "white")
        draw = ImageDraw.Draw(page)
        rects = plan_page_rects(
            len(chunk),
            page_w,
            page_h,
            len(pages),
            layout_file=layout_file,
            fallback_layout_file=fallback_layout_file,
            margin=margin,
            gutter=gutter,
        )
        for path, rect in zip(chunk, rects, strict=True):
            x1, y1, x2, y2 = rect
            with Image.open(path) as img:
                fitted = _panel_image(img, (x2 - x1, y2 - y1))
            page.paste(fitted, (x1, y1))
        for rect in rects:
            draw.rectangle(rect, outline="black", width=border)
        if full_bleed:
            page.save(output_dir / f"{prefix}_{len(pages) + 1:02d}.png")
            pages.append(output_dir / f"{prefix}_{len(pages) + 1:02d}.png")
            continue
        canvas = Image.new("RGB", (width, height), "black")
        if page_blur:
            bg = ImageOps.fit(page, (width, height), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
            bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(30))).enhance(0.6)
            canvas.paste(bg, (0, 0))
        canvas.paste(page, ((width - page_w) // 2, (height - page_h) // 2))
        out = output_dir / f"{prefix}_{len(pages) + 1:02d}.png"
        canvas.save(out)
        pages.append(out)
    return pages
