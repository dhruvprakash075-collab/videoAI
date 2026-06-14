"""Minimal Notion API client for local workspace use.

The client reads `NOTION_TOKEN` from the environment. It is intentionally tiny
so the workspace can read from or write to Notion without adding a new service
layer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests

NOTION_API_VERSION = "2022-06-28"


class NotionError(RuntimeError):
    """Raised when a Notion request fails."""


@dataclass(slots=True)
class NotionClient:
    """Small wrapper around the Notion REST API."""

    token: str
    api_version: str = NOTION_API_VERSION

    @classmethod
    def from_env(cls) -> NotionClient:
        token = os.getenv("NOTION_TOKEN", "").strip()
        if not token:
            raise NotionError("NOTION_TOKEN is not set")
        return cls(token=token)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": self.api_version,
            "Content-Type": "application/json",
        }

    def request(self, method: str, endpoint: str, *, json: dict | None = None) -> dict:
        url = f"https://api.notion.com/v1{endpoint}"
        resp = requests.request(method, url, headers=self._headers(), json=json, timeout=30)
        if resp.status_code >= 400:
            raise NotionError(f"Notion API error {resp.status_code}: {resp.text}")
        if not resp.text:
            return {}
        return resp.json()

    def retrieve_user(self) -> dict:
        return self.request("GET", "/users/me")

    def list_databases(self, page_size: int = 10) -> dict:
        return self.request(
            "POST",
            "/search",
            json={"page_size": page_size, "filter": {"value": "database", "property": "object"}},
        )

    def list_pages(self, page_size: int = 10) -> dict:
        return self.request(
            "POST",
            "/search",
            json={"page_size": page_size, "filter": {"value": "page", "property": "object"}},
        )


def get_notion_client() -> NotionClient:
    """Return a Notion client configured from the environment."""

    return NotionClient.from_env()

