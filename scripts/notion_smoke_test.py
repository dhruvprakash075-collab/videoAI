#!/usr/bin/env python3
"""Quick smoke test for a local Notion token setup."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils.notion_client import NotionClient, NotionError


def main() -> int:
    try:
        client = NotionClient.from_env()
        me = client.retrieve_user()
        print(json.dumps({"ok": True, "user": me.get("name"), "id": me.get("id")}, indent=2))
        return 0
    except NotionError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
