"""Import Roboflow manga panel labels into panel layout JSON.

Supports YOLO box labels and YOLO segmentation labels with normalized coords.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _bbox_from_label(line: str) -> tuple[float, float, float, float] | None:
    parts = [float(p) for p in line.split()]
    if len(parts) == 5:
        _, cx, cy, w, h = parts
        return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    if len(parts) >= 7 and len(parts[1:]) % 2 == 0:
        xs = parts[1::2]
        ys = parts[2::2]
        return min(xs), min(ys), max(xs), max(ys)
    return None


def _clamp_rect(rect: tuple[float, float, float, float]) -> list[float]:
    return [round(max(0.0, min(1.0, v)), 4) for v in rect]


def _label_files(root: Path) -> list[Path]:
    labels = root / "labels"
    if labels.is_dir():
        return sorted(labels.glob("*.txt"))
    return sorted(root.rglob("labels/*.txt"))


def import_layouts(dataset_root: Path, output: Path, *, max_panels: int = 5) -> list[dict]:
    layouts = []
    seen = set()
    for label_file in _label_files(dataset_root):
        rects = []
        for line in label_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rect = _bbox_from_label(line)
                if rect:
                    rects.append(_clamp_rect(rect))
        rects = sorted(rects, key=lambda r: (r[1], r[0]))[:max_panels]
        if not rects:
            continue
        key = json.dumps(rects)
        if key in seen:
            continue
        seen.add(key)
        layouts.append({"name": f"roboflow_{len(layouts) + 1:03d}", "panels": rects})

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(layouts, indent=2), encoding="utf-8")
    return layouts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_root", type=Path)
    parser.add_argument("--output", type=Path, default=Path("config/panel_layouts.roboflow.json"))
    args = parser.parse_args()
    layouts = import_layouts(args.dataset_root, args.output)
    print(f"wrote {len(layouts)} layouts to {args.output}")


if __name__ == "__main__":
    main()
