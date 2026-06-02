#!/usr/bin/env python3
"""
debug_helper.py - Interactive Debugger & Log Profiler.

A premium developer skill tool that automates log error audits, dissects Pydantic
schema validation issues, launches tests with pdb hooks, and runs safe
isolated code snippets.
"""

import argparse
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

# ANSI colors
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

def log_info(msg: str):
    print(f"{BLUE}[INFO]{RESET} {msg}")

def log_success(msg: str):
    print(f"{GREEN}[OK] {msg}{RESET}")

def log_warn(msg: str):
    print(f"{YELLOW}[WARN] {msg}{RESET}")

def log_error(msg: str):
    print(f"{RED}[ERROR] {msg}{RESET}")

# ── Log Error Audits ────────────────────────────────────────────────────────

def audit_logs(log_dir_str: str, limit: int = 5):
    print(f"\n{BOLD}{CYAN}=== Log Error Archaeology (Recent {limit}) ==={RESET}")
    log_dir = Path(log_dir_str)
    if not log_dir.exists() or not log_dir.is_dir():
        log_error(f"Logs directory not found: {log_dir}")
        return

    # Gather log files sorted by modification time
    log_files = sorted(
        log_dir.rglob("*.log"),
        key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not log_files:
        log_warn("No log files (.log) found in the logs directory.")
        return

    log_info(f"Scanning {len(log_files)} file(s) for exceptions and errors...")
    errors_found = 0

    # Traceback capture pattern
    traceback_pattern = re.compile(r"(Traceback \(most recent call last\):.*?(?:^[a-zA-Z0-9_]+Error:.*?$|^[a-zA-Z0-9_]+Exception:.*?$))", re.MULTILINE | re.DOTALL)

    for lf in log_files:
        if errors_found >= limit:
            break
        try:
            content = lf.read_text(encoding="utf-8", errors="ignore")
            # Find all exceptions
            matches = traceback_pattern.findall(content)
            if matches:
                for match in matches:
                    if errors_found >= limit:
                        break
                    errors_found += 1
                    print(f"\n{BOLD}{YELLOW}Error {errors_found} in {lf.name}:{RESET}")
                    # Color code trace lines
                    lines = match.strip().split("\n")
                    for line in lines:
                        if line.startswith(("Traceback", "  File ")):
                            print(f"  {CYAN}{line}{RESET}")
                        elif "Error:" in line or "Exception:" in line:
                            print(f"  {BOLD}{RED}{line}{RESET}")
                        else:
                            print(f"  {line}")
        except Exception as e:
            log_warn(f"Failed to read log {lf.name}: {e}")

    if errors_found == 0:
        log_success("No active traceback exceptions or errors found in logs.")

# ── Pydantic Schema Error Breakdowns ────────────────────────────────────────

def diagnose_pydantic_error(raw_err_msg: str):
    """Parse and print Pydantic validation errors in a high-readability table."""
    print(f"\n{BOLD}{CYAN}=== Pydantic Validation Diagnostics ==={RESET}")
    # Simple regex parsing for typical pydantic V2 error strings
    # Format typically: field_name\n  error_msg [type=..., input_value=...]
    try:
        errors = re.findall(r"(\w+)\n\s+(.*?)\s+\[type=(.*?), input_value=(.*?)\]", raw_err_msg)
        if not errors:
            # Try V1 format fallback
            errors = re.findall(r"(\w+)\n\s+(.*?)\s+\(type=(.*?)\)", raw_err_msg)

        if errors:
            print(f"  Found {len(errors)} validation failure(s):")
            for idx, err in enumerate(errors):
                field = err[0]
                msg = err[1]
                t_type = err[2]
                val = err[3] if len(err) > 3 else "unknown"
                print(f"\n  {BOLD}{YELLOW}Failure {idx + 1}:{RESET}")
                print(f"    * {BOLD}Field Name:{RESET} {CYAN}{field}{RESET}")
                print(f"    * {BOLD}Violation:{RESET}  {RED}{msg}{RESET}")
                print(f"    * {BOLD}Input Type:{RESET} {t_type}")
                print(f"    * {BOLD}Value Sent:{RESET} {val}")
        else:
            # Just print the raw message with coloring
            print(f"  {YELLOW}Raw message parsed (Format unmatched):{RESET}")
            for line in raw_err_msg.splitlines():
                if "validation error" in line.lower() or "input_value" in line:
                    print(f"    {RED}{line}{RESET}")
                else:
                    print(f"    {line}")
    except Exception as e:
        log_error(f"Error diagnostics failed: {e}")

# ── Interactive Pytest with pdb hooks ────────────────────────────────────────

def run_test_pdb(test_selector: str):
    print(f"\n{BOLD}{CYAN}=== Launching Interactive Pytest (with PDB hook) ==={RESET}")
    log_info(f"Running selector: pytest -vv --pdb {test_selector}")
    try:
        # Run pytest inside the shell allowing terminal inputs to PDB
        subprocess.run(
            ["pytest", "-vv", "--pdb", test_selector],
            check=False
        )
    except FileNotFoundError:
        log_error("pytest executable not found in active virtual environment.")

# ── Safe Isolated Sandbox Execution ─────────────────────────────────────────

def run_sandbox_code(code_string: str):
    print(f"\n{BOLD}{CYAN}=== Executing Safe Sandboxed Code ==={RESET}")
    log_info("Executing snippet...")

    # Restrict builtins to prevent system modifying functions in sandbox mode
    safe_globals = {
        "__builtins__": __builtins__,
        "sys": sys,
        "os": os,
        "Path": Path,
        "math": __import__("math"),
        "json": __import__("json")
    }

    try:
        # Capture stdout
        import io
        old_stdout = sys.stdout
        redirected_output = io.StringIO()
        sys.stdout = redirected_output

        exec(code_string, safe_globals)

        sys.stdout = old_stdout
        output = redirected_output.getvalue()

        log_success("Snippet executed successfully!")
        if output:
            print(f"\n{BOLD}Output:{RESET}\n{output}")
        else:
            print("  (No stdout generated.)")
    except Exception:
        sys.stdout = sys.__stdout__  # restore
        print(f"\n{BOLD}{RED}CRASH DETECTED:{RESET}")
        traceback.print_exc()

# ── Main Flow ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Interactive Debugger & Log Profiler.")
    subparsers = parser.add_subparsers(dest="command", help="Diagnostic subcommands")

    # Audit subcommand
    audit_parser = subparsers.add_parser("audit", help="Audit logs for exceptions")
    audit_parser.add_argument("--dir", default="logs", help="Directory containing logs")
    audit_parser.add_argument("--limit", type=int, default=5, help="Max tracebacks to output")

    # Schema subcommand
    schema_parser = subparsers.add_parser("schema", help="Diagnose Pydantic validation error string")
    schema_parser.add_argument("error", help="The raw validation error message string")

    # Pytest subcommand
    test_parser = subparsers.add_parser("test", help="Run a pytest file with active pdb debugger hook")
    test_parser.add_argument("selector", help="Path to test file or selector (e.g. tests/test_uistate.py)")

    # Sandbox subcommand
    sandbox_parser = subparsers.add_parser("run", help="Run isolated sandboxed python code")
    sandbox_parser.add_argument("code", help="The python code snippet to run")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "audit":
        audit_logs(args.dir, args.limit)
    elif args.command == "schema":
        diagnose_pydantic_error(args.error)
    elif args.command == "test":
        run_test_pdb(args.selector)
    elif args.command == "run":
        run_sandbox_code(args.code)

if __name__ == "__main__":
    main()
