"""Import a local Kaggle Anime Face Dataset export into a face reference pool."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def import_faces(source: Path, output: Path, *, limit: int = 200) -> int:
    output.mkdir(parents=True, exist_ok=True)
    images = [p for p in sorted(source.rglob("*")) if p.is_file() and p.suffix.lower() in EXTS]
    for i, src in enumerate(images[:limit], start=1):
        shutil.copy2(src, output / f"anime_face_{i:04d}{src.suffix.lower()}")
    return min(len(images), limit)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path("reference_assets/face_reference_pools/anime_face"))
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    count = import_faces(args.source, args.output, limit=args.limit)
    print(f"copied {count} faces to {args.output}")


if __name__ == "__main__":
    main()
