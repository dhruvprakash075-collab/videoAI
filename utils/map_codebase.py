#!/usr/bin/env python3
"""
map_codebase.py - Structural Codebase Mapping and Steerability Utility.

An AST-based code analysis tool that parses classes, methods, docstrings, and
import dependencies to give the AI agent and the developer an intelligent,
hierarchical overview of the project architecture.

Safe for all platforms, including Windows terminals with legacy encodings.
"""

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any

# Reconfigure stdout to UTF-8 on Windows if possible
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Determine if we should use Unicode box-drawing characters
# We check stdout encoding or let it catch encoding errors dynamically.
USE_UNICODE = True
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8", "cp65001"):
    USE_UNICODE = False

# Tree branch characters
T_BRANCH = "├──" if USE_UNICODE else "|--"
V_LINE = "│" if USE_UNICODE else "|"
L_BRANCH = "└──" if USE_UNICODE else "\\--"
INDENT = "  "

# ANSI colors
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


class ProjectMapper:
    def __init__(self, root_dir: Path):
        self.root_dir = root_dir.resolve()
        self.map_data: dict[str, Any] = {}

    def analyze_file(self, file_path: Path) -> dict[str, Any]:
        """Parse a python file using AST and extract structured information."""
        rel_path = file_path.relative_to(self.root_dir).as_posix()
        try:
            content = file_path.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(file_path))
        except Exception as e:
            return {"error": f"Failed to parse AST: {e}"}

        file_info = {
            "path": rel_path,
            "docstring": ast.get_docstring(tree) or "",
            "classes": [],
            "functions": [],
            "imports": [],
        }

        # Traverse the AST nodes
        for node in tree.body:
            # 1. Classes
            if isinstance(node, ast.ClassDef):
                class_info = {
                    "name": node.name,
                    "docstring": ast.get_docstring(node) or "",
                    "methods": [],
                    "bases": [ast.unparse(b) for b in node.bases],
                }
                for sub_node in node.body:
                    if isinstance(sub_node, ast.FunctionDef):
                        # Extract method arguments
                        args = [a.arg for a in sub_node.args.args]
                        method_info = {
                            "name": sub_node.name,
                            "args": args,
                            "docstring": ast.get_docstring(sub_node) or "",
                        }
                        class_info["methods"].append(method_info)
                file_info["classes"].append(class_info)

            # 2. Top-level Functions
            elif isinstance(node, ast.FunctionDef):
                args = [a.arg for a in node.args.args]
                func_info = {
                    "name": node.name,
                    "args": args,
                    "docstring": ast.get_docstring(node) or "",
                }
                file_info["functions"].append(func_info)

            # 3. Imports
            elif isinstance(node, ast.Import):
                for name in node.names:
                    file_info["imports"].append(name.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for name in node.names:
                    file_info["imports"].append(f"{module}.{name.name}")

        return file_info

    def scan_project(self, target_dirs: list[str]) -> dict[str, Any]:
        """Scan specified directories recursively for Python files."""
        self.map_data = {}
        for d in target_dirs:
            dir_path = self.root_dir / d
            if not dir_path.exists() or not dir_path.is_dir():
                continue

            for file_path in dir_path.rglob("*.py"):
                if "__pycache__" in file_path.parts or ".venv" in file_path.parts:
                    continue
                file_info = self.analyze_file(file_path)
                self.map_data[file_info["path"]] = file_info

        return self.map_data

    def render_tree(self):
        """Render a beautiful, high-readability architectural tree in the terminal."""
        try:
            print(f"\n{BOLD}{CYAN}=== Video.AI Structural Architecture Map ==={RESET}")

            # Group files by their top-level directory
            by_dir: dict[str, list[str]] = {}
            for rel_path in sorted(self.map_data.keys()):
                parts = rel_path.split("/")
                group = parts[0] if len(parts) > 1 else "root"
                by_dir.setdefault(group, []).append(rel_path)

            for group, files in sorted(by_dir.items()):
                print(f"\n{BOLD}{BLUE}[Folder: {group}/]{RESET}")
                for f in files:
                    info = self.map_data[f]
                    filename = Path(f).name
                    print(f"  {T_BRANCH} {BOLD}{GREEN}{filename}{RESET}")

                    # Print docstring summary if present
                    if info.get("docstring"):
                        summary = info["docstring"].strip().split("\n")[0][:80]
                        print(f"  {V_LINE}   {YELLOW}Description: {summary}...{RESET}")

                    # Print classes
                    for c in info.get("classes", []):
                        bases_str = f"({', '.join(c['bases'])})" if c["bases"] else ""
                        print(
                            f"  {V_LINE}   {T_BRANCH} Class: {BOLD}{CYAN}{c['name']}{RESET}{bases_str}"
                        )
                        for m in c.get("methods", []):
                            args_str = ", ".join(m["args"])
                            print(
                                f"  {V_LINE}   {V_LINE}   {T_BRANCH} method: {m['name']}({args_str})"
                            )

                    # Print functions
                    for fn in info.get("functions", []):
                        args_str = ", ".join(fn["args"])
                        print(
                            f"  {V_LINE}   {T_BRANCH} function: {BOLD}{fn['name']}{RESET}({args_str})"
                        )

                    # Print structural imports (Video.AI internal ones)
                    internal_imports = [
                        imp
                        for imp in info.get("imports", [])
                        if imp.startswith(("utils", "core", "audio", "video", "memory", "agents"))
                    ]
                    if internal_imports:
                        print(
                            f"  {V_LINE}   {L_BRANCH} Internal Deps: {', '.join(internal_imports[:5])}{'...' if len(internal_imports) > 5 else ''}"
                        )
        except Exception as e:
            # Absolute fallback if printing ANSI or character symbols fails
            print(f"Failed to print tree structurally ({e}). Standard raw JSON output below:")
            print(json.dumps(self.map_data, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Map project files to AST model.")
    parser.add_argument("--json", action="store_true", help="Output raw structural JSON data.")
    parser.add_argument("--file", type=str, help="Map and diagnose a specific single Python file.")
    args = parser.parse_args()

    root = Path(__file__).parent.parent
    mapper = ProjectMapper(root)

    if args.file:
        file_path = Path(args.file).resolve()
        if not file_path.exists():
            print(f"File not found: {args.file}")
            sys.exit(1)
        info = mapper.analyze_file(file_path)
        if args.json:
            print(json.dumps(info, indent=2))
        else:
            print(f"\n{BOLD}{CYAN}=== AST Analysis of {file_path.name} ==={RESET}")
            print(f"Docstring: {info.get('docstring')}")
            print("\nClasses:")
            for c in info.get("classes", []):
                print(f"  - {c['name']} (Inherits: {c['bases']})")
                for m in c.get("methods", []):
                    print(f"    * {m['name']}({', '.join(m['args'])})")
            print("\nFunctions:")
            for fn in info.get("functions", []):
                print(f"  - {fn['name']}({', '.join(fn['args'])})")
            print("\nImports:")
            print(f"  {', '.join(info.get('imports', []))}")
        sys.exit(0)

    # Scan standard project folders
    mapper.scan_project(["core", "audio", "video", "utils", "memory", "agents"])

    if args.json:
        print(json.dumps(mapper.map_data, indent=2))
    else:
        mapper.render_tree()


if __name__ == "__main__":
    main()
