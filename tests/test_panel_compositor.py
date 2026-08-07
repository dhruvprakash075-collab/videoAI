from pathlib import Path

from PIL import Image

from video.image_gen.panel_compositor import (
    _layout_rects,
    _valid_rects,
    compose_character_sheet,
    compose_panel_pages,
    page_canvas_size,
    plan_page_counts,
    plan_page_rects,
)


def test_plan_page_counts_walks_dataset_in_order(tmp_path: Path):
    layout_file = tmp_path / "layouts.json"
    layout_file.write_text(
        '[{"name":"a","panels":[[0,0,0.5,0.5],[0.5,0,1,0.5],[0,0.5,0.5,1],[0.5,0.5,1,1]]},'
        '{"name":"b","panels":[[0,0,1,0.5],[0,0.5,1,1],[0,0,1,1]]}]'
    )
    assert plan_page_counts(10, layout_file) == [4, 3, 3]


def test_plan_page_counts_cycles_and_fits_last_page(tmp_path: Path):
    layout_file = tmp_path / "layouts.json"
    layout_file.write_text(
        '[{"name":"a","panels":[[0,0,1,0.5],[0,0.5,1,1],[0,0,1,1]]},'
        '{"name":"b","panels":[[0,0,1,1],[0,0,1,1]]}]'
    )
    # cycle back to a 3-panel page, then fit the 1 leftover in a 2-panel slot
    assert plan_page_counts(5, layout_file) == [3, 2]


def test_plan_page_counts_remainder_when_no_layout_fits(tmp_path: Path):
    layout_file = tmp_path / "layouts.json"
    layout_file.write_text('[{"name":"a","panels":[[0,0,1,0.5],[0,0.5,1,1],[0,0,1,1]]}]')
    # only 3-panel layouts exist; 7 images → 3+3+1 via the fixed grid
    assert plan_page_counts(7, layout_file) == [3, 3, 1]


def test_plan_page_counts_defaults_to_five_panels():
    assert plan_page_counts(7, None) == [5, 2]
    assert plan_page_counts(10, None) == [5, 5]


def test_compose_panel_pages_mixed_counts_per_page(tmp_path: Path):
    """Pages take the next dataset layout's panel count — not a fixed 5."""
    layout_file = tmp_path / "layouts.json"
    layout_file.write_text(
        '[{"name":"four","panels":[[0,0,0.5,0.5],[0.5,0,1,0.5],[0,0.5,0.5,1],[0.5,0.5,1,1]]},'
        '{"name":"two","panels":[[0,0,1,0.5],[0,0.5,1,1]]}]'
    )
    srcs = []
    for i, color in enumerate(["red", "blue", "green", "yellow", "black", "white"], start=1):
        path = tmp_path / f"src_{i}.png"
        Image.new("RGB", (64, 64), color).save(path)
        srcs.append(path)

    pages = compose_panel_pages(
        srcs, tmp_path, width=400, height=200, border=4, layout_file=layout_file, page_aspect=0
    )

    assert len(pages) == 2
    page1 = Image.open(pages[0]).convert("RGB")
    page2 = Image.open(pages[1]).convert("RGB")
    # page 1 = 4-panel grid: red in top-left panel, yellow in bottom-right
    assert page1.getpixel((50, 50)) == (255, 0, 0)
    assert page1.getpixel((350, 150)) == (255, 255, 0)
    # page 2 = 2-panel layout: black top, white bottom
    assert page2.getpixel((200, 50)) == (0, 0, 0)
    assert page2.getpixel((200, 150)) == (255, 255, 255)


def test_compose_panel_pages_uses_distinct_images(tmp_path: Path):
    srcs = []
    for i, color in enumerate(["red", "blue"], start=1):
        path = tmp_path / f"src_{i}.png"
        Image.new("RGB", (64, 64), color).save(path)
        srcs.append(path)

    pages = compose_panel_pages(srcs, tmp_path, width=400, height=200, margin=20, gutter=20, border=4, page_aspect=0)

    assert len(pages) == 1
    out = Image.open(pages[0]).convert("RGB")
    assert out.getpixel((20, 20)) == (0, 0, 0)
    assert out.getpixel((40, 100)) == (255, 0, 0)
    assert out.getpixel((220, 100)) == (0, 0, 255)


def test_compose_panel_pages_uses_layout_file(tmp_path: Path):
    layout_file = tmp_path / "layouts.json"
    layout_file.write_text('[{"name":"two_rows","panels":[[0.1,0.1,0.9,0.45],[0.1,0.55,0.9,0.9]]}]')
    srcs = []
    for i, color in enumerate(["red", "blue"], start=1):
        path = tmp_path / f"src_{i}.png"
        Image.new("RGB", (64, 64), color).save(path)
        srcs.append(path)

    pages = compose_panel_pages(srcs, tmp_path, width=400, height=200, border=4, layout_file=layout_file, page_aspect=0)

    out = Image.open(pages[0]).convert("RGB")
    assert out.getpixel((40, 20)) == (0, 0, 0)
    assert out.getpixel((200, 50)) == (255, 0, 0)
    assert out.getpixel((200, 130)) == (0, 0, 255)


def test_compose_panel_pages_uses_fallback_layout_file(tmp_path: Path):
    fallback = tmp_path / "fallback.json"
    fallback.write_text('[{"name":"one","panels":[[0.2,0.2,0.8,0.8]]}]')
    src = tmp_path / "src.png"
    Image.new("RGB", (64, 64), "red").save(src)

    pages = compose_panel_pages([src], tmp_path, width=100, height=100, border=2, layout_file=tmp_path / "missing.json", fallback_layout_file=fallback, page_aspect=0)

    out = Image.open(pages[0]).convert("RGB")
    assert out.getpixel((20, 20)) == (0, 0, 0)
    assert out.getpixel((50, 50)) == (255, 0, 0)


def test_compose_accepts_str_layout_file_values(tmp_path: Path):
    """String layout_file values (raw config strings, as run_storyboard passes)
    must not crash on .is_file() — regression from the storyboard smoke run."""
    missing = str(tmp_path / "missing.json")
    fallback = str(tmp_path / "fallback.json")
    (tmp_path / "fallback.json").write_text('[{"name":"one","panels":[[0.2,0.2,0.8,0.8]]}]')
    src = tmp_path / "src.png"
    Image.new("RGB", (64, 64), "red").save(src)

    pages = compose_panel_pages([src], tmp_path / "sheet", width=100, height=100, border=2, layout_file=missing, fallback_layout_file=fallback, page_aspect=0)

    assert len(pages) == 1
    assert pages[0].is_file()


def test_compose_panel_pages_skips_overlapping_dataset_layout(tmp_path: Path):
    layout_file = tmp_path / "layouts.json"
    layout_file.write_text(
        '['
        '{"name":"bad","panels":[[0.1,0.1,0.8,0.8],[0.2,0.2,0.9,0.9]]},'
        '{"name":"good","panels":[[0.1,0.1,0.45,0.9],[0.55,0.1,0.9,0.9]]}'
        ']'
    )
    srcs = []
    for i, color in enumerate(["red", "blue"], start=1):
        path = tmp_path / f"src_{i}.png"
        Image.new("RGB", (64, 64), color).save(path)
        srcs.append(path)

    pages = compose_panel_pages(srcs, tmp_path, width=100, height=100, border=2, layout_file=layout_file, page_aspect=0)

    out = Image.open(pages[0]).convert("RGB")
    assert out.getpixel((20, 50)) == (255, 0, 0)
    assert out.getpixel((70, 50)) == (0, 0, 255)


def test_compose_panel_pages_fills_wide_panel_with_center_crop(tmp_path: Path):
    """A portrait source in a wide panel is center-cropped to fill — no
    letterbox, no blurred fill bars (the old contain+blur behavior)."""
    src = tmp_path / "portrait.png"
    img = Image.new("RGB", (100, 200), "green")
    for y in range(200):
        for x in range(45, 55):
            img.putpixel((x, y), (255, 0, 0))
    img.save(src)

    pages = compose_panel_pages([src], tmp_path, width=400, height=120, margin=10, border=2, page_aspect=0)

    out = Image.open(pages[0]).convert("RGB")
    assert out.getpixel((10, 10)) == (0, 0, 0)
    # fit scale = 380/100 = 3.8 -> stripe (src x 45-55) lands at target x 171-209
    assert out.getpixel((190, 60)) == (255, 0, 0)
    # PIL named color 'green' is #008000
    assert out.getpixel((50, 60)) == (0, 128, 0)


def test_compose_panel_pages_a4_centered_on_blurred_bg(tmp_path: Path):
    """A4 page centered on the frame; leftover screen is a blurred, darkened
    copy of the page — not white, not black."""
    srcs = []
    for i, color in enumerate(["red", "blue"], start=1):
        path = tmp_path / f"src_{i}.png"
        Image.new("RGB", (64, 64), color).save(path)
        srcs.append(path)

    pages = compose_panel_pages(srcs, tmp_path, width=400, height=200, margin=20, gutter=20, border=4)

    out = Image.open(pages[0]).convert("RGB")
    page_w, page_h = page_canvas_size(400, 200, 20, 1.414)
    assert (page_w, page_h) == (113, 160)
    left, top = (400 - page_w) // 2, (200 - page_h) // 2
    # panel 1 (fixed 2-panel grid) is red, panel 2 blue
    assert out.getpixel((left + 30, top + 80)) == (255, 0, 0)
    assert out.getpixel((left + 80, top + 80)) == (0, 0, 255)
    # page margin is white
    assert out.getpixel((left + 8, top + page_h - 8)) == (255, 255, 255)
    # leftover screen left of the page: blurred dark copy, not white/black
    bg = out.getpixel((left - 10, top + 80))
    assert bg not in ((255, 255, 255), (0, 0, 0))


def test_page_canvas_size_full_bleed_when_aspect_zero():
    assert page_canvas_size(1920, 1080, 48, 0) == (1920, 1080)
    assert page_canvas_size(1920, 1080, 48, 1.414) == (696, 984)


def test_layout_selection_rotates_through_valid_layouts(tmp_path: Path):
    """Page index must rotate through the dataset — not always pick the same layout."""
    layout_file = tmp_path / "layouts.json"
    layout_file.write_text(
        '[{"name":"grid2x2","panels":[[0,0,0.5,0.5],[0.5,0,1,0.5],[0,0.5,0.5,1],[0.5,0.5,1,1]]},'
        '{"name":"four_rows","panels":[[0,0,1,0.25],[0,0.25,1,0.5],[0,0.5,1,0.75],[0,0.75,1,1]]}]'
    )

    first = _layout_rects(layout_file, 4, 400, 400, 0)
    second = _layout_rects(layout_file, 4, 400, 400, 1)

    assert first[0] == (0, 0, 200, 200)
    assert second[0] == (0, 0, 400, 100)
    assert first != second


def test_layout_selection_wraps_rotation_and_skips_invalid(tmp_path: Path):
    """Rotation wraps modulo; layouts failing validity are skipped, not preferred."""
    layout_file = tmp_path / "layouts.json"
    layout_file.write_text(
        '[{"name":"bad","panels":[[0,0,0.8,0.8],[0.2,0.2,0.9,0.9]]},'
        '{"name":"good","panels":[[0,0,0.5,0.5],[0.5,0,1,0.5],[0,0.5,0.5,1],[0.5,0.5,1,1]]}]'
    )

    page0 = _layout_rects(layout_file, 4, 100, 100, 0)
    page2 = _layout_rects(layout_file, 4, 100, 100, 2)

    assert page0 == page2  # 2 valid layouts → rotation wraps with period 2
    assert page0[0] == (0, 0, 50, 50)


def test_plan_page_rects_chains_layout_then_fallback_then_fixed(tmp_path: Path):
    """plan_page_rects resolves the same chain compose_panel_pages used to inline."""
    fallback = tmp_path / "fallback.json"
    fallback.write_text('[{"name":"one","panels":[[0.2,0.2,0.8,0.8]]}]')

    from_layout = plan_page_rects(
        1, 100, 100, 0, layout_file=fallback, fallback_layout_file=tmp_path / "nope.json"
    )
    assert from_layout == [(20, 20, 80, 80)]

    from_fixed = plan_page_rects(2, 400, 200, 0, margin=20, gutter=20)
    assert from_fixed == [(20, 20, 190, 180), (210, 20, 380, 180)]


def test_plan_page_rects_matches_compose_geometry(tmp_path: Path):
    """The rects plan_page_rects returns must equal what compose_panel_pages uses."""
    layout_file = tmp_path / "layouts.json"
    layout_file.write_text(
        '[{"name":"two_rows","panels":[[0.1,0.1,0.9,0.45],[0.1,0.55,0.9,0.9]]}]'
    )

    planned = plan_page_rects(2, 400, 200, 0, layout_file=layout_file)

    assert planned == [(40, 20, 360, 90), (40, 110, 360, 180)]


def test_valid_rects_accepts_small_panel_between_1_5_and_3_percent():
    """A panel that's 2.25% of page area should be valid with 1.5% threshold."""
    page_w = page_h = 100  # page_area = 10000
    # Panel (0, 0, 15, 15) → area = 225 → 2.25% (>1.5%, <3%)
    assert _valid_rects([(0, 0, 15, 15)], page_w, page_h)


def test_valid_rects_rejects_panel_below_1_5_percent():
    """A panel under 1.5% of page area is annotation noise → rejected."""
    page_w = page_h = 100  # page_area = 10000
    # Panel (0, 0, 10, 10) → area = 100 → 1.0% (<1.5%)
    assert not _valid_rects([(0, 0, 10, 10)], page_w, page_h)
    # 1x1 speck → 0.01%
    assert not _valid_rects([(0, 0, 1, 1)], page_w, page_h)


def test_valid_rects_accepts_tiny_overlap_between_2_and_5_percent():
    """Two panels overlapping by ~2% should be valid with 5% tolerance."""
    page_w = page_h = 100
    # Panel A: (0, 0, 49, 100)  → area = 4900
    # Panel B: (48, 0, 100, 100) → overlap x=48-49=1 → overlap area = 1*100 = 100
    # 100 / 4900 = 2.04%  → rejected at 2%, accepted at 5%
    assert _valid_rects([(0, 0, 49, 100), (48, 0, 100, 100)], page_w, page_h)


def test_compose_panel_pages_draws_labels(tmp_path: Path):
    """Labels darken the panel corner vs an unlabeled compose (storyboard captions)."""
    srcs = []
    for i, color in enumerate(["white", "white"], start=1):
        path = tmp_path / f"src_{i}.png"
        Image.new("RGB", (64, 64), color).save(path)
        srcs.append(path)

    pages = compose_panel_pages(
        srcs, tmp_path / "labeled", width=400, height=200, margin=20, gutter=20,
        border=4, page_aspect=0, labels=["1 · wide", "2 · close-up"],
    )
    plain = compose_panel_pages(
        srcs, tmp_path / "plain", width=400, height=200, margin=20, gutter=20,
        border=4, page_aspect=0,
    )

    out = Image.open(pages[0]).convert("RGB")
    ref = Image.open(plain[0]).convert("RGB")
    # Chip interior: label bg is black (fill=(0,0,0,160)) where an unlabeled
    # panel is white. (26,29) sits inside the chip, off the glyphs, off border.
    x, y = 26, 29
    assert ref.getpixel((x, y)) == (255, 255, 255)
    assert sum(out.getpixel((x, y))) < sum(ref.getpixel((x, y)))


def test_compose_character_sheet_4_views_and_name(tmp_path: Path):
    """Character sheet: grey canvas, 4 labeled views, char name in corner."""
    views = []
    for i, color in enumerate(["red", "blue", "green", "yellow"], start=1):
        path = tmp_path / f"view_{i}.png"
        Image.new("RGB", (64, 64), color).save(path)
        views.append(path)

    sheet = compose_character_sheet(
        *views, tmp_path / "sheet.png", char_name="Hero", width=400, height=400
    )

    out = Image.open(sheet).convert("RGB")
    # grey canvas fills the margins
    assert out.getpixel((2, 2)) == (96, 96, 96)
    # 2x2 view grid, sampling cell interiors away from borders and label chips:
    # front (red) top-left, portrait (green) top-right,
    # back (blue) bottom-left, side profile (yellow) bottom-right
    assert out.getpixel((27, 130)) == (255, 0, 0)
    assert out.getpixel((300, 130)) == (0, 128, 0)
    assert out.getpixel((27, 300)) == (0, 0, 255)
    assert out.getpixel((300, 300)) == (255, 255, 0)
