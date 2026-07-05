"""researcher.py - Multi-source topic research for the source-path pipeline.

Gathers up to ``research.budget`` HTTP responses per ``research_topic()`` call
across three sources, returns them as a ranked list of :class:`ResearchItem`:

  * ``wikipedia`` - Wikipedia REST API (``/api/rest_v1/page/summary/{title}``).
                    Wraps the title-based search in a two-step flow: action
                    API search for titles, then REST summary for each title.
  * ``wikimedia``  - Wikimedia action API (``/w/api.php?action=opensearch``).
                    Returns the raw titles list as research items.
  * ``rss``        - ``feedparser`` over each URL in ``research.rss_urls``.

A User-Agent header is **always** sent (mandatory per Wikimedia's ToS). Each
source is invoked once per ``research_topic()`` call (Wikipedia's two-step
counts as one budget unit) and failures are isolated: a 500 from Wikipedia
does not break RSS.

Configuration (read from the merged ``config`` dict, with safe defaults):

  * ``research.enabled``            - master switch; ``False`` returns ``[]``
  * ``research.sources``            - ordered list of sources to try
  * ``research.budget``             - max HTTP calls per invocation (default 3)
  * ``research.timeout_s``          - per-call wall-clock timeout (default 15)
  * ``research.per_source_limit``   - max items returned per source (default 3)
  * ``research.rss_urls``           - list of feed URLs to try
  * ``research.user_agent``         - User-Agent header (default set per ToS)

The function is a pure dispatcher + small per-source helpers. All HTTP is
mockable via ``patch("requests.get", ...)`` or ``patch.dict(sys.modules, ...)``
in tests.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


WIKIPEDIA_REST_BASE = "https://en.wikipedia.org/api/rest_v1"
WIKIMEDIA_API_BASE = "https://en.wikipedia.org/w/api.php"
DEFAULT_USER_AGENT = "VideoAI/6.0 (+https://github.com/...)"
SUPPORTED_SOURCES = ("wikipedia", "wikimedia", "rss")


class ResearchError(Exception):
    """Raised when research cannot run (bad config, no sources enabled, etc.)."""


@dataclass
class ResearchItem:
    """One piece of research output, normalized across sources."""

    title: str
    text: str
    url: str
    source_type: str
    relevance_score: float = 0.0


def _score(item_text: str, query: str) -> float:
    """Trivial relevance score: word-overlap ratio (case-insensitive).

    Returns a value in [0.0, 1.0]. A score of 0 means no overlap; 1.0 means
    every query word appears at least once in the item text.
    """
    q_words = {w.lower() for w in query.split() if len(w) >= 3}
    if not q_words:
        return 0.0
    text_lower = item_text.lower()
    hits = sum(1 for w in q_words if w in text_lower)
    return round(hits / len(q_words), 4)


def _headers(config: dict) -> dict:
    ua = (config.get("research") or {}).get("user_agent") or DEFAULT_USER_AGENT
    return {"User-Agent": ua, "Accept": "application/json"}


def _timeout(config: dict) -> float:
    return float((config.get("research") or {}).get("timeout_s", 15))


def _per_source_limit(config: dict) -> int:
    return int((config.get("research") or {}).get("per_source_limit", 3))


def _fetch_wikipedia_rest(query: str, config: dict) -> list[ResearchItem]:
    """Wikipedia REST: action-API search -> REST summary per title (1 budget unit)."""
    try:
        import requests
    except ImportError:
        log.debug("[researcher] requests not installed; skipping wikipedia")
        return []

    timeout = _timeout(config)
    headers = _headers(config)
    limit = _per_source_limit(config)

    try:
        # Classification: fixed trusted public API (Wikipedia)
        search_resp = requests.get(
            WIKIMEDIA_API_BASE,
            params={
                "action": "opensearch",
                "search": query,
                "limit": str(limit),
                "format": "json",
            },
            headers=headers,
            timeout=timeout,
        )
        search_resp.raise_for_status()
    except Exception as e:
        log.debug(f"[researcher] Wikipedia search failed for {query!r}: {e}")
        return []

    try:
        # opensearch returns [query, [titles], [descriptions], [urls]]
        _, titles, _descriptions, urls = search_resp.json()
    except Exception as e:
        log.debug(f"[researcher] Wikipedia search parse failed: {e}")
        return []

    items: list[ResearchItem] = []
    for i, title in enumerate(titles[:limit]):
        url = urls[i] if i < len(urls) else ""
        summary_url = f"{WIKIPEDIA_REST_BASE}/page/summary/{_quote_title(title)}"
        try:
            # Classification: fixed trusted public API (Wikipedia REST)
            r = requests.get(summary_url, headers=headers, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            text = data.get("extract", "") or data.get("description", "")
            page_url = data.get("content_urls", {}).get("desktop", {}).get("page", url)
            if not text:
                continue
            items.append(
                ResearchItem(
                    title=title,
                    text=text,
                    url=page_url or url,
                    source_type="wikipedia",
                    relevance_score=_score(text, query),
                )
            )
        except Exception as e:
            log.debug(f"[researcher] Wikipedia summary failed for {title!r}: {e}")

    return items


def _quote_title(title: str) -> str:
    try:
        from urllib.parse import quote
    except ImportError:
        return title.replace(" ", "_")
    return quote(title.replace(" ", "_"), safe="()_,")


def _fetch_wikimedia_rest(query: str, config: dict) -> list[ResearchItem]:
    """Wikimedia action API: opensearch returns titles + URLs (1 budget unit)."""
    try:
        import requests
    except ImportError:
        log.debug("[researcher] requests not installed; skipping wikimedia")
        return []

    timeout = _timeout(config)
    headers = _headers(config)
    limit = _per_source_limit(config)

    try:
        # Classification: fixed trusted public API (Wikipedia)
        resp = requests.get(
            WIKIMEDIA_API_BASE,
            params={
                "action": "opensearch",
                "search": query,
                "limit": str(limit),
                "namespace": "0",
                "format": "json",
            },
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
    except Exception as e:
        log.debug(f"[researcher] Wikimedia fetch failed for {query!r}: {e}")
        return []

    try:
        _query, titles, _descriptions, urls = resp.json()
    except Exception as e:
        log.debug(f"[researcher] Wikimedia parse failed: {e}")
        return []

    items: list[ResearchItem] = []
    for i, title in enumerate(titles[:limit]):
        url = urls[i] if i < len(urls) else ""
        if not title or not url:
            continue
        items.append(
            ResearchItem(
                title=title,
                text=title,
                url=url,
                source_type="wikimedia",
                relevance_score=_score(title, query),
            )
        )
    return items


def _fetch_rss(query: str, config: dict) -> list[ResearchItem]:
    """Iterate ``research.rss_urls``; each feed parse = 1 budget unit.

    All feeds are tried under a single budget unit (the function returns up to
    ``per_source_limit`` items aggregated across all feeds).
    """
    try:
        import feedparser
    except ImportError:
        log.debug("[researcher] feedparser not installed; skipping rss")
        return []

    feeds = (config.get("research") or {}).get("rss_urls") or []
    if not feeds:
        return []

    headers = _headers(config)
    limit = _per_source_limit(config)

    items: list[ResearchItem] = []
    for feed_url in feeds:
        try:
            parsed = feedparser.parse(feed_url, agent=headers.get("User-Agent", ""))
        except Exception as e:
            log.debug(f"[researcher] RSS parse failed for {feed_url}: {e}")
            continue
        if getattr(parsed, "bozo", 0) and not parsed.entries:
            continue
        for entry in parsed.entries[:limit]:
            title = getattr(entry, "title", "") or ""
            text = getattr(entry, "summary", "") or getattr(entry, "description", "") or title
            url = getattr(entry, "link", "") or ""
            if not title and not text:
                continue
            if query.lower() not in (title + " " + text).lower():
                continue
            items.append(
                ResearchItem(
                    title=title or url,
                    text=text,
                    url=url,
                    source_type="rss",
                    relevance_score=_score(text or title, query),
                )
            )
            if len(items) >= limit:
                return items
    return items


_SOURCE_FETCHERS = {
    "wikipedia": _fetch_wikipedia_rest,
    "wikimedia": _fetch_wikimedia_rest,
    "rss": _fetch_rss,
}


def research_topic(query: str, config: dict) -> list[ResearchItem]:
    """Fetch research items for ``query`` across the configured sources.

    Returns a list of :class:`ResearchItem` sorted by ``relevance_score``
    descending. Returns ``[]`` when research is disabled, the query is empty,
    or no source returns results.
    """
    query = (query or "").strip()
    if not query:
        return []
    rcfg = config.get("research") or {}
    if not rcfg.get("enabled", True):
        return []

    sources = rcfg.get("sources") or list(SUPPORTED_SOURCES)
    sources = [s for s in sources if s in _SOURCE_FETCHERS]
    if not sources:
        return []

    budget = int(rcfg.get("budget", 3))
    if budget <= 0:
        return []

    items: list[ResearchItem] = []
    seen_keys: set = set()

    for source in sources:
        if budget <= 0:
            break
        if source not in _SOURCE_FETCHERS:
            continue
        try:
            new_items = _SOURCE_FETCHERS[source](query, config)
        except Exception as e:
            log.debug(f"[researcher] source {source!r} crashed: {e}")
            new_items = []
        budget -= 1
        for it in new_items:
            key = it.url or (it.source_type, it.title)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            items.append(it)

    items.sort(key=lambda x: x.relevance_score, reverse=True)
    return items
