from pathlib import Path

from PIL import Image

from video.image_gen.panel_compositor import compose_panel_pages


def test_compose_panel_pages_uses_distinct_images(tmp_path: Path):
    srcs = []
    for i, color in enumerate(["red", "blue"], start=1):
        path = tmp_path / f"src_{i}.png"
        Image.new("RGB", (64, 64), color).save(path)
        srcs.append(path)

    pages = compose_panel_pages(srcs, tmp_path, width=400, height=200, margin=20, gutter=20, border=4)

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

    pages = compose_panel_pages(srcs, tmp_path, width=400, height=200, border=4, layout_file=layout_file)

    out = Image.open(pages[0]).convert("RGB")
    assert out.getpixel((40, 20)) == (0, 0, 0)
    assert out.getpixel((200, 50)) == (255, 0, 0)
    assert out.getpixel((200, 130)) == (0, 0, 255)


def test_compose_panel_pages_uses_fallback_layout_file(tmp_path: Path):
    fallback = tmp_path / "fallback.json"
    fallback.write_text('[{"name":"one","panels":[[0.2,0.2,0.8,0.8]]}]')
    src = tmp_path / "src.png"
    Image.new("RGB", (64, 64), "red").save(src)

    pages = compose_panel_pages([src], tmp_path, width=100, height=100, border=2, layout_file=tmp_path / "missing.json", fallback_layout_file=fallback)

    out = Image.open(pages[0]).convert("RGB")
    assert out.getpixel((20, 20)) == (0, 0, 0)
    assert out.getpixel((50, 50)) == (255, 0, 0)


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

    pages = compose_panel_pages(srcs, tmp_path, width=100, height=100, border=2, layout_file=layout_file)

    out = Image.open(pages[0]).convert("RGB")
    assert out.getpixel((20, 50)) == (255, 0, 0)
    assert out.getpixel((70, 50)) == (0, 0, 255)
