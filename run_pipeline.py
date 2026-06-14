#!/usr/bin/env python3
"""Run the pipeline CLI from the repository root."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


def main() -> None:
    repo_root = str(Path(__file__).parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    from bootstrap_pipeline import bootstrap

    bootstrap()
    runpy.run_module("core.pipeline_long", run_name="__main__")


if __name__ == "__main__":
    main()
