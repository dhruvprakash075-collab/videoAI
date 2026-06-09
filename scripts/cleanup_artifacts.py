#!/usr/bin/env python3
"""
Cleanup script for Video.AI artifacts.

Removes:
- Old temp files in codex_tmp, cache, temp_srt_files
- Old logs (older than --days-old, default 7 days)
- Failed job logs (from jobs where status=failed)
- Stale output files (older than --days-old-output)
- Empty directories

Usage:
    python scripts/cleanup_artifacts.py              # dry-run (safe)
    python scripts/cleanup_artifacts.py --days-old 14
    python scripts/cleanup_artifacts.py --clean-all   # actually delete
"""

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def get_old_files(directory: Path, days_old: int) -> list[Path]:
    """Get files older than N days."""
    if not directory.exists():
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - (days_old * 86400)
    old_files = []
    for f in directory.rglob("*"):
        if f.is_file() and f.stat().st_mtime < cutoff:
            old_files.append(f)
    return old_files


def remove_temp_dirs(dry_run: bool = True, clean_cache: bool = False) -> int:
    """Remove temp directories. cache/ is only removed if clean_cache=True."""
    temp_dirs = [Path("codex_tmp"), Path("temp_srt_files")]
    if clean_cache:
        temp_dirs.append(Path("cache"))
    removed = 0
    for d in temp_dirs:
        if d.exists():
            if dry_run:
                file_count = len([f for f in d.rglob("*") if f.is_file()])
                print(f"  [DRY] Would remove {d}: {file_count} files")
            else:
                try:
                    shutil.rmtree(d)
                    print(f"  Removed: {d}")
                    removed += 1
                except Exception as e:
                    print(f"  Failed to remove {d}: {e}")
    return removed


def remove_old_logs(days_old: int = 7, dry_run: bool = True) -> int:
    """Remove old log files."""
    logs_dir = REPO_ROOT / "logs"
    if not logs_dir.exists():
        return 0

    old_files = get_old_files(logs_dir, days_old)
    if dry_run:
        total_size = sum(f.stat().st_size for f in old_files) / (1024**2)
        print(f"  [DRY] Would remove {len(old_files)} old logs ({total_size:.1f} MB)")
    else:
        removed = 0
        for f in old_files:
            try:
                f.unlink()
                removed += 1
            except Exception as e:
                print(f"    Failed to remove {f}: {e}")
        return removed
    return 0


def remove_failed_job_logs(dry_run: bool = True) -> int:
    """Remove logs for failed jobs."""
    try:
        db_path = REPO_ROOT / "studio_projects" / "jobs" / "video_ai_jobs.db"
        if not db_path.exists():
            return 0

        with sqlite3.connect(db_path) as conn:
            cur = conn.cursor()
            rows = cur.execute("SELECT topic FROM jobs WHERE status='failed'").fetchall()

        removed = 0
        for (topic,) in rows:
            if not topic:
                continue
            log_dir = REPO_ROOT / "logs" / topic
            if log_dir.exists():
                if dry_run:
                    file_count = len([f for f in log_dir.rglob("*") if f.is_file()])
                    print(f"  [DRY] Would remove failed job logs for '{topic}': {file_count} files")
                else:
                    try:
                        shutil.rmtree(log_dir)
                        print(f"  Removed failed job logs: {log_dir}")
                        removed += 1
                    except Exception as e:
                        print(f"    Failed to remove {log_dir}: {e}")
        return removed
    except Exception as e:
        print(f"  Error accessing job DB: {e}")
        return 0


def remove_stale_outputs(days_old: int = 30, dry_run: bool = True) -> int:
    """Remove old output files from studio_outputs."""
    outputs_dir = REPO_ROOT / "studio_outputs"
    if not outputs_dir.exists():
        return 0

    old_files = get_old_files(outputs_dir, days_old)
    if dry_run:
        total_size = sum(f.stat().st_size for f in old_files) / (1024**2)
        print(f"  [DRY] Would remove {len(old_files)} stale outputs ({total_size:.1f} MB)")
    else:
        removed = 0
        for f in old_files:
            try:
                f.unlink()
                removed += 1
            except Exception as e:
                print(f"    Failed to remove {f}: {e}")
        return removed
    return 0


def remove_empty_dirs(dry_run: bool = True) -> int:
    """Remove empty directories."""
    dirs_to_check = [
        REPO_ROOT / "logs",
        REPO_ROOT / "studio_outputs",
    ]
    removed = 0
    for root_dir in dirs_to_check:
        if not root_dir.exists():
            continue
        for d in sorted(root_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if d.is_dir() and not any(d.iterdir()):
                if dry_run:
                    print(f"  [DRY] Would remove empty dir: {d}")
                else:
                    try:
                        d.rmdir()
                        removed += 1
                    except Exception:
                        pass
    return removed


def main():
    """Run cleanup."""
    parser = argparse.ArgumentParser(description="Clean up Video.AI artifacts")
    parser.add_argument("--days-old", type=int, default=7, help="Logs older than N days (default: 7)")
    parser.add_argument("--days-old-output", type=int, default=30, help="Outputs older than N days (default: 30)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="clean_all", action="store_false", default=False, help="Show what would be removed (default)")
    mode.add_argument("--clean-all", action="store_true", help="Actually remove files")
    parser.add_argument("--clean-cache", action="store_true", help="Also remove cache/ directory (usually expensive to regenerate)")
    args = parser.parse_args()

    dry_run = not args.clean_all

    print("\n" + "=" * 70)
    print(f"Video.AI Cleanup ({'DRY RUN' if dry_run else 'LIVE CLEANUP'})".center(70))
    print("=" * 70 + "\n")

    total_removed = 0
    for task_fn, desc in [
        (lambda: remove_temp_dirs(dry_run, args.clean_cache), "temp dirs"),
        (lambda: remove_old_logs(args.days_old, dry_run), "old logs"),
        (lambda: remove_failed_job_logs(dry_run), "failed job logs"),
        (lambda: remove_stale_outputs(args.days_old_output, dry_run), "stale outputs"),
        (lambda: remove_empty_dirs(dry_run), "empty dirs"),
    ]:
        try:
            count = task_fn()
            total_removed += count
            print()
        except Exception as e:
            print(f"  Error in {desc}: {e}\n")

    print("=" * 70)
    if dry_run:
        print(f"DRY RUN: Would have processed ~{total_removed} items".center(70))
        print("Re-run with --clean-all to actually remove files".center(70))
    else:
        print(f"Cleanup complete: {total_removed} items removed".center(70))
    print("=" * 70 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
