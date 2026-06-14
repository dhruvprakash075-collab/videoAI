import os
from pathlib import Path

ALLOWLIST = {"utils/vram.py", "utils/tempfiles.py", "studio_tui.py"}  # justified cleanup/tools only

def test_no_broad_suppress_in_logic_paths():
    repo_root = Path(__file__).resolve().parents[2]
    hits = []
    
    # Walk the repository
    for root, dirs, files in os.walk(repo_root):
        # Skip tests, venv, git, cache, and external dependency dirs
        dirs[:] = [d for d in dirs if d not in ("tests", "venv", ".git", ".venv", "__pycache__", ".pytest_cache", "external")]
        
        for file in files:
            if not file.endswith(".py"):
                continue
                
            file_path = Path(root) / file
            rel_path = file_path.relative_to(repo_root).as_posix()
            
            if rel_path in ALLOWLIST:
                continue
                
            try:
                content = file_path.read_text(encoding="utf-8")
                # Look for contextlib.suppress(Exception) or suppress(Exception)
                # We can check for 'suppress(Exception)' in a robust way
                for line_idx, line in enumerate(content.splitlines(), start=1):
                    # Remove comments and whitespace
                    clean_line = line.split("#")[0].strip()
                    if "suppress(Exception)" in clean_line.replace(" ", ""):
                        hits.append(f"{rel_path}:{line_idx}:{line.strip()}")
            except Exception as e:
                # If we cannot read a file (e.g. permission), skip or log it
                pass
                
    assert hits == [], "broad suppress outside allowlist:\n" + "\n".join(hits)
