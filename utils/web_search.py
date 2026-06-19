"""web_search.py - Research story/character context from Wikipedia + DuckDuckGo.

Spoiler-avoidance: only fetches setting/introduction/premise info, skips plot
resolution, endings, and character fate sections. Merges results into a
structured dict that the Director uses for context-aware pre-production.

Uses requests + BeautifulSoup (already installed via crewai/transformers deps).

Used by: director_agent.py (pre-production Phase 1)
"""

import logging
import re
import urllib.error
import urllib.parse
import urllib.request

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_DDG_HTML = "https://html.duckduckgo.com/html/"  # DDG API deprecated 2024; use HTML endpoint
_WIKI_API = "https://en.wikipedia.org/w/api.php"
_HEADERS = {"User-Agent": "VideoAI/1.0 (research bot; contact@example.com)"}

# Wikipedia sections to INCLUDE (setting/character info, not plot spoilers)
_WIKI_INCLUDE_SECTIONS = [
    "premise",
    "setting",
    "overview",
    "introduction",
    "background",
    "universe",
    "world",
    "concept",
    "characters",
    "protagonist",
    "concept and creation",
    "development",
    "design",
    "publication",
    "publication history",
    "inspiration",
    "themes",
    "style",
    "style and themes",
    "worldbuilding",
    "lore",
    "cosmology",
    "description",
    "appearance",
    "abilities",
    "powers",
    "personality",
    "role",
    "characteristics",
    "profile",
    "gameplay",
    "mechanics",
]

# Wikipedia sections to SKIP (plot spoilers)
_WIKI_SKIP_SECTIONS = [
    "plot",
    "synopsis",
    "story",
    "episodes",
    "chapters",
    "ending",
    "conclusion",
    "sequel",
    "death",
    "arc",
    "history",
    "biography",
    "career",
    "trivia",
    "reception",
    "legacy",
    "spin-off",
    "adaptation",
    "controvers",
    "criticism",
    "prequel",
    "volumes",
    "volume list",
    "anime",
    "film",
    "media",
    "manga",
    "novel",
]

_SPOILER_REGEX = [
    re.compile(r"(?i)dies?\s+(in|at|during|after|when)"),
    re.compile(r"(?i)killed\s+(by|in|during|after)"),
    re.compile(r"(?i)(final|last)\s+(battle|chapter|episode|arc)"),
    re.compile(r"(?i)ultimately\s+(defeats?|kills?|destroys?)"),
    re.compile(r"(?i)reveals?\s+to\s+be"),
    re.compile(r"(?i)it\s+is\s+revealed\s+that"),
    re.compile(r"(?i)in\s+the\s+end[,.!]"),
    re.compile(r"(?i)eventually\s+(discovers?|finds?|learns?|realizes?)"),
    re.compile(r"(?i)is\s+actually\s+(the|a)\b"),
]

_SPOILER_LEADING = re.compile(
    r"(?i)^(the ending|in the finale|the final battle|the series concludes|"
    r"ultimately|eventually|the novel ends|in the final chapter)"
)


def _filter_sections(text: str, topic: str = "") -> str:
    """Filter Wikipedia extract by sections: keep INCLUDE, skip SKIP, strip spoilers.

    Uses exact section matching (not substring) to avoid conflicts where a skip
    keyword like 'history' would incorrectly drop 'publication history' (an include).
    """
    if not text:
        return ""
    lines = text.split("\n")
    sections = []
    current_section = "lead"
    current_lines = []

    for line in lines:
        # Wikipedia section headers are lines starting with =
        if line.startswith("=") and line.endswith("="):
            section_name = line.strip("= ").lower()
            if current_lines:
                section_text = "\n".join(current_lines)
                # Exact match: check include first, then exact skip
                if current_section in _WIKI_INCLUDE_SECTIONS or current_section == "lead":
                    sections.append(section_text)
                elif current_section not in _WIKI_SKIP_SECTIONS:
                    # Neutral section: include but strip spoilers
                    clean = _strip_spoilers(section_text, topic)
                    if clean:
                        sections.append(clean)
                current_lines = []
            current_section = section_name
        else:
            current_lines.append(line)

    # Handle last section
    if current_lines:
        section_text = "\n".join(current_lines)
        if current_section in _WIKI_INCLUDE_SECTIONS or current_section == "lead":
            sections.append(section_text)
        elif current_section not in _WIKI_SKIP_SECTIONS:
            clean = _strip_spoilers(section_text, topic)
            if clean:
                sections.append(clean)

    result = "\n\n".join(sections)
    if len(result) > 5000:
        result = result[:5000] + "..."
    return result


def _wiki_api_request(params: dict) -> dict:
    """Make a Wikipedia API request. Classification: fixed trusted public API."""
    resp = requests.get(_WIKI_API, params=params, headers=_HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _search_wikipedia(query: str) -> list[dict]:
    """Search Wikipedia and return spoiler-safe results."""
    results = []

    # Step 1: Search for pages
    try:
        search_data = _wiki_api_request(
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": 5,
                "format": "json",
            }
        )
        search_results = search_data.get("query", {}).get("search", [])
    except Exception as e:
        log.debug(f"Wikipedia search failed for '{query}': {e}")
        return results

    for sr in search_results[:3]:
        title = sr.get("title", "")
        pageid = sr.get("pageid", 0)
        if not title or not pageid:
            continue

        # Step 2: Get full page extract with sections (not just intro)
        try:
            extract_data = _wiki_api_request(
                {
                    "action": "query",
                    "prop": "extracts|info",
                    "explaintext": "1",
                    "exsectionformat": "wiki",
                    "exlimit": "max",
                    "inprop": "url",
                    "pageids": pageid,
                    "format": "json",
                }
            )
            pages = extract_data.get("query", {}).get("pages", {})
            page = pages.get(str(pageid), {})
            extract = page.get("extract", "")
            url = page.get("fullurl", "")

            if extract and extract.strip() and not extract.startswith("may refer to:"):
                safe_text = _filter_sections(extract, query)
                results.append(
                    {
                        "source": "wikipedia",
                        "title": title,
                        "summary": safe_text,
                        "url": url,
                    }
                )
                log.debug(f"Wikipedia: fetched '{title}' ({len(safe_text)} chars)")
        except Exception as e:
            log.debug(f"Wikipedia extract failed for '{title}': {e}")

    return results


def _search_duckduckgo(query: str) -> list[dict]:
    """Search DuckDuckGo via HTML endpoint (API was deprecated in 2024)."""
    data = urllib.parse.urlencode({"q": query}).encode("utf-8")
    headers = {**_HEADERS, "Content-Type": "application/x-www-form-urlencoded"}

    results = []
    try:
        req = urllib.request.Request(_DDG_HTML, data=data, headers=headers)
        # P3-17 fix: urlopen RAISES urllib.error.HTTPError on 403 — it never returns
        # a response object with .status == 403. Retry once after a short delay.
        try:
            resp_ctx = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as e:
            if e.code == 403:
                log.warning("DDG returned 403 — retrying after 3s delay...")
                import time

                time.sleep(3)
                req2 = urllib.request.Request(_DDG_HTML, data=data, headers=headers)
                resp_ctx = urllib.request.urlopen(req2, timeout=10)
            else:
                raise

        with resp_ctx as resp:
            html_text = resp.read().decode("utf-8", errors="ignore")
        if "captcha" in html_text.lower() or "g-recaptcha" in html_text.lower():
            log.warning("DDG CAPTCHA detected — skipping DDG this run")
            return results

        soup = BeautifulSoup(html_text, "html.parser")
        result_divs = (
            soup.select(".result") or soup.select(".web-result") or soup.select(".results_links")
        )
        if not result_divs:
            log.debug("DuckDuckGo: no result elements found (HTML may have changed)")
            return results
        for result_div in result_divs[:5]:
            title_el = (
                result_div.select_one(".result__title a")
                or result_div.select_one(".result__a")
                or result_div.select_one("a.result-link")
            )
            snippet_el = (
                result_div.select_one(".result__snippet")
                or result_div.select_one(".result-snippet")
                or result_div.select_one(".snippet")
            )
            link_el = result_div.select_one(".result__url")

            title = title_el.get_text(strip=True) if title_el else ""
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            link = link_el.get("href", "") if link_el else ""

            if snippet:
                results.append(
                    {
                        "source": "duckduckgo",
                        "title": title or query,
                        "summary": _strip_spoilers(snippet, query),
                        "url": link,
                    }
                )

        log.debug(f"DuckDuckGo (HTML): {len(results)} results for '{query}'")
    except Exception as e:
        log.debug(f"DuckDuckGo HTML search failed for '{query}': {e}")

    return results


def _strip_spoilers(text: str, topic: str = "") -> str:
    """Remove paragraphs/sentences that contain spoiler patterns."""
    if not text:
        return ""

    paragraphs = text.split("\n")
    safe_paragraphs = []

    for para in paragraphs:
        para_stripped = para.strip()
        if not para_stripped:
            continue

        has_spoiler = any(rx.search(para_stripped) for rx in _SPOILER_REGEX)
        if has_spoiler:
            continue
        if _SPOILER_LEADING.match(para_stripped):
            continue

        safe_paragraphs.append(para_stripped)

    result = "\n\n".join(safe_paragraphs)
    if len(result) > 2500:
        result = result[:2500] + "..."
    return result


def search_story_web(topic: str, search_extra: list[str] | None = None) -> dict:
    """Search the web for story/character context across multiple sources.

    Returns:
        Dict with keys: topic, wikipedia_results, ddg_results, combined_summary
    """
    log.info(f"[Web Search] Researching '{topic}'...")

    queries = [topic]
    if search_extra:
        queries.extend(search_extra)

    all_wiki = []
    all_ddg = []

    from concurrent.futures import ThreadPoolExecutor

    for query in queries:
        with ThreadPoolExecutor(max_workers=2) as pool:
            wiki_future = pool.submit(_search_wikipedia, query)
            ddg_future = pool.submit(_search_duckduckgo, query)
            try:
                wiki_results = wiki_future.result(timeout=20)
            except Exception:
                wiki_results = []
            try:
                ddg_results = ddg_future.result(timeout=15)
            except Exception:
                ddg_results = []

        seen_wiki_titles = {r["title"] for r in all_wiki}
        for r in wiki_results:
            if r["title"] not in seen_wiki_titles:
                all_wiki.append(r)
                seen_wiki_titles.add(r["title"])

        # DDG dedup: use URL as key when present; fall back to (title, summary) tuple
        # when URL is empty so distinct results with empty URLs are not dropped.
        seen_ddg_keys: set = set()
        for r in all_ddg:
            url = r.get("url", "")
            key = url if url else (r.get("title", ""), r.get("summary", ""))
            seen_ddg_keys.add(key)
        for r in ddg_results:
            url = r.get("url", "")
            key = url if url else (r.get("title", ""), r.get("summary", ""))
            if key not in seen_ddg_keys:
                all_ddg.append(r)
                seen_ddg_keys.add(key)

    combined_parts = []
    for r in all_wiki:
        combined_parts.append(f"[Wikipedia: {r['title']}] {r['summary']}")
    for r in all_ddg:
        if r.get("summary", "").strip():
            combined_parts.append(f"[Web: {r.get('title', '')}] {r['summary']}")

    combined = "\n\n".join(combined_parts[:10])
    if len(combined) > 4000:
        combined = combined[:4000] + "..."

    log.info(f"[Web Search] Complete: {len(all_wiki)} Wikipedia + {len(all_ddg)} DDG results")

    return {
        "topic": topic,
        "wikipedia_results": all_wiki,
        "ddg_results": all_ddg,
        "combined_summary": combined,
        "result_count": len(all_wiki) + len(all_ddg),
    }
