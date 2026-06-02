#!/usr/bin/env python3
"""Run the pipeline with the specified topic.

P4-27 fix: use Path(__file__).parent instead of hardcoded C:/Video.AI;
           avoid clobbering sys.argv by calling run_long_pipeline directly.
"""
import sys
from pathlib import Path

# Add repo root to sys.path so imports work regardless of cwd
_repo_root = str(Path(__file__).parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from bootstrap_pipeline import bootstrap

bootstrap()

from core.pipeline_long import run_long_pipeline

result = run_long_pipeline(
    topic="Real Hero",
    duration_min=10,
    resume=False,
)

print(f"Status: {result.get('status', 'unknown').upper()}")
if result.get("output"):
    print(f"Output: {result.get('output')}")
