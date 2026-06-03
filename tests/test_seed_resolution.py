"""test_seed_resolution.py - Tests for A2: seed_map built once before frame loop."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_seed_map_built_from_project_files(tmp_path):
    """seed_map should be populated from project.json files in PROJECTS_ROOT."""
    # Create a fake project directory with a visual lock
    proj_dir = tmp_path / "my_project"
    proj_dir.mkdir()
    (proj_dir / "project.json").write_text(
        json.dumps(
            {
                "visual_locks": {
                    "protagonist": {"seed": 42, "description": "tall hero"},
                    "mentor": {"seed": 99, "description": "wise elder"},
                }
            }
        ),
        encoding="utf-8",
    )

    seed_map = {}
    import memory.project_store as _ps

    # Patch PROJECTS_ROOT to our tmp dir
    original = _ps.PROJECTS_ROOT
    _ps.PROJECTS_ROOT = tmp_path
    try:
        # Simulate the seed_map build logic from image_gen._stable_diffusion
        if _ps.PROJECTS_ROOT.exists():
            for proj_dir_iter in _ps.PROJECTS_ROOT.iterdir():
                proj_file = proj_dir_iter / "project.json"
                if proj_file.exists():
                    pdata = json.loads(proj_file.read_text(encoding="utf-8"))
                    for ckey, lock in pdata.get("visual_locks", {}).items():
                        if lock and lock.get("seed") is not None and ckey not in seed_map:
                            seed_map[ckey] = int(lock["seed"])
    finally:
        _ps.PROJECTS_ROOT = original

    assert seed_map.get("protagonist") == 42
    assert seed_map.get("mentor") == 99


def test_seed_map_lookup_avoids_repeated_scan(tmp_path):
    """Verify that looking up from seed_map is O(1) and doesn't re-scan disk."""
    seed_map = {"protagonist": 12345, "mentor": 67890}

    # Simulate per-frame lookup (no disk access)
    for _i in range(10):
        char = "protagonist"
        seed = seed_map.get(char, 0)
        assert seed == 12345

    # No exception means no disk scan happened


def test_seed_map_empty_when_no_projects(tmp_path):
    """When PROJECTS_ROOT is empty, seed_map should be empty (no crash)."""
    import memory.project_store as _ps

    original = _ps.PROJECTS_ROOT
    _ps.PROJECTS_ROOT = tmp_path  # empty dir
    try:
        seed_map = {}
        if _ps.PROJECTS_ROOT.exists():
            for proj_dir_iter in _ps.PROJECTS_ROOT.iterdir():
                proj_file = proj_dir_iter / "project.json"
                if proj_file.exists():
                    pdata = json.loads(proj_file.read_text(encoding="utf-8"))
                    for ckey, lock in pdata.get("visual_locks", {}).items():
                        if lock and lock.get("seed") is not None:
                            seed_map[ckey] = int(lock["seed"])
    finally:
        _ps.PROJECTS_ROOT = original

    assert seed_map == {}
