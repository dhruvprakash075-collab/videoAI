#!/usr/bin/env python3
"""
git_helper.py - Git Automation & Code Archaeology Tool.

Provides an AST and Git API to let the AI agent and the developer inspect
workspace modifications, perform commits, trace diffs, and view history in a
structured, high-readability terminal format.
"""

import subprocess
import sys

# ANSI colors
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

def run_git(args: list) -> str:
    """Safely execute a local Git command and return output."""
    try:
        res = subprocess.run(
            ["git", *args],
            capture_output=True, text=True, check=True, encoding="utf-8"
        )
        return res.stdout.strip()
    except subprocess.CalledProcessError as e:
        # Return stderr details on failure
        return f"ERROR: {e.stderr.strip()}"
    except FileNotFoundError:
        return "ERROR: Git executable not found in system PATH."

def get_status():
    print(f"\n{BOLD}{CYAN}=== Git Repository Status ==={RESET}")
    status = run_git(["status", "--porcelain"])
    if not status:
        print(f"  {GREEN}✔ Workspace is clean. Nothing to commit!{RESET}")
        return

    lines = status.split("\n")
    print(f"  Found {len(lines)} modified/untracked file(s):")
    for line in lines:
        if not line.strip():
            continue
        code = line[:2]
        filepath = line[3:]

        # Color code according to Git status
        if "M" in code:
            print(f"    * [{YELLOW}Modified{RESET}]  {filepath}")
        elif "A" in code:
            print(f"    * [{GREEN}Added{RESET}]     {filepath}")
        elif "??" in code:
            print(f"    * [{RED}Untracked{RESET}] {filepath}")
        elif "D" in code:
            print(f"    * [{RED}Deleted{RESET}]   {filepath}")
        else:
            print(f"    * [{CYAN}{code.strip()}{RESET}]   {filepath}")

def get_diff():
    print(f"\n{BOLD}{CYAN}=== Structural Code Diffs ==={RESET}")
    diff = run_git(["diff", "--stat"])
    if not diff:
        print("  No unstaged modifications found.")
        return
    print(diff)
    print(f"\n{BOLD}Detailed Diffs (Summary):{RESET}")
    detailed = run_git(["diff", "-U1"])
    lines = detailed.split("\n")
    for line in lines[:50]:  # Limit output length
        if line.startswith("+") and not line.startswith("+++"):
            print(f"{GREEN}{line}{RESET}")
        elif line.startswith("-") and not line.startswith("---"):
            print(f"{RED}{line}{RESET}")
        elif line.startswith("@@"):
            print(f"{CYAN}{line}{RESET}")
        else:
            print(line)

    if len(lines) > 50:
        print(f"  {YELLOW}... truncated {len(lines) - 50} lines of diff (use git diff for full output) ...{RESET}")

def commit_changes(message: str):
    print(f"\n{BOLD}{CYAN}=== Automating Workspace Commit ==={RESET}")
    if not message or not message.strip():
        print(f"  {RED}ERROR: Commit message cannot be empty.{RESET}")
        return

    # Check status first
    status = run_git(["status", "--porcelain"])
    if not status:
        print("  Nothing to commit, workspace is clean.")
        return

    print("  1. Staging all modifications (git add .)...")
    run_git(["add", "."])

    print(f"  2. Committing with message: '{BOLD}{message}{RESET}'...")
    res = run_git(["commit", "-m", message])
    if "ERROR" in res:
        print(f"  {RED}Commit failed: {res}{RESET}")
    else:
        print(f"  {GREEN}✔ Success! Commit completed.{RESET}")
        print(res)

def get_history(limit: int = 5):
    print(f"\n{BOLD}{CYAN}=== Recent Repository History (Last {limit}) ==={RESET}")
    log_out = run_git(["log", "-n", str(limit), "--oneline", "--decorate"])
    if "ERROR" in log_out:
        print(f"  {RED}Failed to read log: {log_out}{RESET}")
    else:
        lines = log_out.split("\n")
        for line in lines:
            if not line:
                continue
            parts = line.split(" ", 1)
            sha = parts[0]
            msg = parts[1] if len(parts) > 1 else ""
            print(f"  * {BOLD}{YELLOW}{sha}{RESET} - {msg}")

def main():
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} [status | diff | commit <message> | history [limit]]")
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "status":
        get_status()
    elif cmd == "diff":
        get_diff()
    elif cmd == "commit":
        if len(sys.argv) < 3:
            print(f"Usage: python {sys.argv[0]} commit \"your descriptive message\"")
            sys.exit(1)
        commit_changes(sys.argv[2])
    elif cmd == "history":
        limit = int(sys.argv[2]) if len(sys.argv) >= 3 else 5
        get_history(limit)
    else:
        print(f"Unknown command: {cmd}")
        print(f"Usage: python {sys.argv[0]} [status | diff | commit <message> | history [limit]]")
        sys.exit(1)

if __name__ == "__main__":
    main()
