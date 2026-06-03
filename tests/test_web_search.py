"""test_web_search.py - web_search helpers and search_story_web orchestrator."""

from unittest.mock import MagicMock, patch

import pytest

from utils import web_search

# ── _strip_spoilers ───────────────────────────────────────────────────────────


def test_strip_spoilers_empty():
    assert web_search._strip_spoilers("") == ""
    assert web_search._strip_spoilers(None) == ""


def test_strip_spoilers_keeps_safe_text():
    out = web_search._strip_spoilers("The story begins in a small town. The hero wakes up.")
    assert "small town" in out
    assert "hero wakes up" in out


def test_strip_spoilers_drops_dies_in():
    """Each newline-separated paragraph is checked independently."""
    out = web_search._strip_spoilers(
        "The hero lives.\nThe hero dies in the final battle.\nThe story continues."
    )
    assert "lives" in out
    assert "dies in" not in out
    assert "story continues" in out


def test_strip_spoilers_drops_killed_by():
    out = web_search._strip_spoilers("The villain appears.\nHe is killed by the hero.\nThe end.")
    assert "villain" in out
    assert "killed by" not in out


def test_strip_spoilers_drops_final_battle():
    out = web_search._strip_spoilers("Setup.\nThe final battle occurs.\nResolution.")
    assert "Setup" in out
    assert "final battle" not in out


def test_strip_spoilers_drops_in_the_end():
    out = web_search._strip_spoilers("Begin.\nIn the end, the hero wins.\nContinues.")
    assert "Begin" in out
    assert "In the end" not in out


def test_strip_spoilers_drops_eventually_discovers():
    out = web_search._strip_spoilers("Setup.\nThe hero eventually discovers the truth.\nEnd.")
    assert "Setup" in out
    assert "eventually" not in out


def test_strip_spoilers_drops_reveals_to_be():
    out = web_search._strip_spoilers("Setup.\nThe hero reveals to be the chosen one.\nEnd.")
    assert "Setup" in out
    assert "reveals to be" not in out


def test_strip_spoilers_drops_leading_phrases():
    out = web_search._strip_spoilers("The ending was happy.\nThe setup was tragic.")
    # First paragraph starts with "The ending" — leading spoiler
    assert "The ending" not in out
    assert "setup was tragic" in out


def test_strip_spoilers_truncates_long_text():
    long_text = "a" * 3000
    out = web_search._strip_spoilers(long_text)
    assert len(out) > 2500
    assert out.endswith("...")


def test_strip_spoilers_drops_empty_paragraphs():
    out = web_search._strip_spoilers("Para 1.\n\n\n\nPara 2.")
    assert "Para 1" in out
    assert "Para 2" in out


# ── _filter_sections ──────────────────────────────────────────────────────────


def test_filter_sections_empty():
    assert web_search._filter_sections("") == ""


def test_filter_sections_includes_premise():
    text = "=Premise=\nThe story is about a hero.\n\n=Plot=\nThe hero dies."
    out = web_search._filter_sections(text)
    assert "about a hero" in out
    assert "hero dies" not in out  # plot is a skip section


def test_filter_sections_handles_lead():
    text = "The story is about a hero who saves the day."
    out = web_search._filter_sections(text)
    assert "hero" in out


def test_filter_sections_neutral_section_strips_spoilers():
    """Neutral sections (not in INCLUDE or SKIP) get the _strip_spoilers treatment."""
    text = "=Other=\nThe hero lives.\n\nThe hero dies in the end."
    out = web_search._filter_sections(text)
    assert "hero lives" in out
    assert "dies in" not in out


def test_filter_sections_truncates_at_5000():
    long_text = "=Premise=\n" + ("x" * 6000)
    out = web_search._filter_sections(long_text)
    assert len(out) <= 5010  # 5000 + "..."
    assert out.endswith("...")


def test_filter_sections_publication_history_not_dropped():
    # "publication history" is an include; "history" alone is a skip
    text = "=Publication history=\nThe book was published in 2020."
    out = web_search._filter_sections(text)
    assert "published in 2020" in out


# ── _search_wikipedia ─────────────────────────────────────────────────────────


def test_search_wikipedia_returns_results():
    fake_search = {"query": {"search": [{"title": "X", "pageid": 1}]}}
    fake_extract = {
        "query": {"pages": {"1": {"extract": "=Setting=\nA place.", "fullurl": "http://x"}}}
    }
    with patch.object(web_search, "_wiki_api_request", side_effect=[fake_search, fake_extract]):
        out = web_search._search_wikipedia("test")
    assert len(out) == 1
    assert out[0]["source"] == "wikipedia"
    assert out[0]["title"] == "X"


def test_search_wikipedia_search_failure_returns_empty():
    with patch.object(web_search, "_wiki_api_request", side_effect=RuntimeError("boom")):
        out = web_search._search_wikipedia("test")
    assert out == []


def test_search_wikipedia_extract_failure_skips_page():
    fake_search = {"query": {"search": [{"title": "X", "pageid": 1}]}}
    with patch.object(
        web_search, "_wiki_api_request", side_effect=[fake_search, RuntimeError("extract fail")]
    ):
        out = web_search._search_wikipedia("test")
    assert out == []


def test_search_wikipedia_skips_disambiguation():
    fake_search = {"query": {"search": [{"title": "X", "pageid": 1}]}}
    fake_extract = {
        "query": {"pages": {"1": {"extract": "may refer to: foo, bar", "fullurl": "http://x"}}}
    }
    with patch.object(web_search, "_wiki_api_request", side_effect=[fake_search, fake_extract]):
        out = web_search._search_wikipedia("test")
    assert out == []


def test_search_wikipedia_empty_extract_skipped():
    fake_search = {"query": {"search": [{"title": "X", "pageid": 1}]}}
    fake_extract = {"query": {"pages": {"1": {"extract": "", "fullurl": "http://x"}}}}
    with patch.object(web_search, "_wiki_api_request", side_effect=[fake_search, fake_extract]):
        out = web_search._search_wikipedia("test")
    assert out == []


def test_search_wikipedia_limits_to_three():
    fake_search = {"query": {"search": [{"title": f"Title {i}", "pageid": i} for i in range(1, 6)]}}

    # Only 3 extracts should be requested
    def side_effect(params):
        if params.get("list") == "search":
            return fake_search
        pid = str(params["pageids"])  # pages are keyed by str(pageid)
        return {"query": {"pages": {pid: {"extract": "=Setting=\nX", "fullurl": "http://x"}}}}

    with patch.object(web_search, "_wiki_api_request", side_effect=side_effect):
        out = web_search._search_wikipedia("test")
    assert len(out) == 3


# ── _search_duckduckgo ────────────────────────────────────────────────────────


def test_search_duckduckgo_returns_results():
    html = """
    <div class="result">
        <a class="result__a" href="http://example.com">Title</a>
        <a class="result__url" href="http://example.com">example.com</a>
        <div class="result__snippet">This is a safe snippet about heroes.</div>
    </div>
    """
    with patch("urllib.request.urlopen") as uo:
        uo.return_value.__enter__.return_value.read.return_value = html.encode()
        out = web_search._search_duckduckgo("test")
    assert len(out) >= 1
    assert out[0]["source"] == "duckduckgo"


def test_search_duckduckgo_captcha_returns_empty():
    html = "<html>captcha required</html>"
    with patch("urllib.request.urlopen") as uo:
        uo.return_value.__enter__.return_value.read.return_value = html.encode()
        out = web_search._search_duckduckgo("test")
    assert out == []


def test_search_duckduckgo_handles_exception():
    with patch("urllib.request.urlopen", side_effect=RuntimeError("net fail")):
        out = web_search._search_duckduckgo("test")
    assert out == []


def test_search_duckduckgo_403_retry():
    import urllib.error

    call_count = [0]

    def fake_urlopen(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise urllib.error.HTTPError("http://x", 403, "Forbidden", {}, None)
        from unittest.mock import MagicMock

        ctx = MagicMock()
        ctx.__enter__.return_value.read.return_value = b"<html></html>"
        return ctx

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        web_search._search_duckduckgo("test")
    assert call_count[0] == 2


def test_search_duckduckgo_403_retries_with_sleep():
    """When both attempts fail with 403, the outer except catches the second HTTPError."""
    import urllib.error

    with (
        patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("http://x", 403, "Forbidden", {}, None),
        ),
        patch("time.sleep") as ts,
    ):
        out = web_search._search_duckduckgo("test")
    # sleep called once (between the two 403 attempts)
    assert ts.called
    # The outer try/except swallows the second HTTPError → empty results
    assert out == []


def test_search_duckduckgo_no_results_class():
    html = "<html><body>no results here</body></html>"
    with patch("urllib.request.urlopen") as uo:
        uo.return_value.__enter__.return_value.read.return_value = html.encode()
        out = web_search._search_duckduckgo("test")
    assert out == []


# ── search_story_web ──────────────────────────────────────────────────────────


def test_search_story_web_returns_combined():
    with (
        patch.object(
            web_search,
            "_search_wikipedia",
            return_value=[
                {"source": "wikipedia", "title": "X", "summary": "summary text", "url": "http://x"}
            ],
        ),
        patch.object(
            web_search,
            "_search_duckduckgo",
            return_value=[
                {"source": "duckduckgo", "title": "Y", "summary": "snippet", "url": "http://y"}
            ],
        ),
    ):
        out = web_search.search_story_web("test topic")
    assert out["topic"] == "test topic"
    assert len(out["wikipedia_results"]) == 1
    assert len(out["ddg_results"]) == 1
    assert "summary text" in out["combined_summary"]
    assert out["result_count"] == 2


def test_search_story_web_with_extra_queries():
    with (
        patch.object(web_search, "_search_wikipedia", return_value=[]) as ws,
        patch.object(web_search, "_search_duckduckgo", return_value=[]) as ds,
    ):
        web_search.search_story_web("topic", search_extra=["extra1", "extra2"])
    assert ws.call_count == 3  # main + 2 extras
    assert ds.call_count == 3


def test_search_story_web_dedup_wiki():
    with (
        patch.object(
            web_search,
            "_search_wikipedia",
            return_value=[
                {"source": "wikipedia", "title": "X", "summary": "a", "url": ""},
                {"source": "wikipedia", "title": "X", "summary": "b", "url": ""},
            ],
        ),
        patch.object(web_search, "_search_duckduckgo", return_value=[]),
    ):
        out = web_search.search_story_web("t")
    # Deduped by title
    assert len(out["wikipedia_results"]) == 1


def test_search_story_web_dedup_ddg_by_url():
    with (
        patch.object(web_search, "_search_wikipedia", return_value=[]),
        patch.object(
            web_search,
            "_search_duckduckgo",
            return_value=[
                {"source": "duckduckgo", "title": "A", "summary": "s", "url": "http://x"},
                {"source": "duckduckgo", "title": "A", "summary": "s", "url": "http://x"},
            ],
        ),
    ):
        out = web_search.search_story_web("t")
    assert len(out["ddg_results"]) == 1


def test_search_story_web_dedup_ddg_by_title_summary_when_no_url():
    with (
        patch.object(web_search, "_search_wikipedia", return_value=[]),
        patch.object(
            web_search,
            "_search_duckduckgo",
            return_value=[
                {"source": "duckduckgo", "title": "A", "summary": "s", "url": ""},
                {"source": "duckduckgo", "title": "A", "summary": "s", "url": ""},
            ],
        ),
    ):
        out = web_search.search_story_web("t")
    assert len(out["ddg_results"]) == 1


def test_search_story_web_dedup_ddg_keeps_distinct():
    with (
        patch.object(web_search, "_search_wikipedia", return_value=[]),
        patch.object(
            web_search,
            "_search_duckduckgo",
            return_value=[
                {"source": "duckduckgo", "title": "A", "summary": "s1", "url": ""},
                {"source": "duckduckgo", "title": "A", "summary": "s2", "url": ""},
            ],
        ),
    ):
        out = web_search.search_story_web("t")
    # Distinct summaries → kept
    assert len(out["ddg_results"]) == 2


def test_search_story_web_handles_search_failure():
    with (
        patch.object(web_search, "_search_wikipedia", side_effect=RuntimeError("boom")),
        patch.object(web_search, "_search_duckduckgo", return_value=[]),
    ):
        out = web_search.search_story_web("t")
    assert out["wikipedia_results"] == []


def test_search_story_web_combined_truncated():
    """Very long combined output is truncated to 4000 chars."""
    long_summary = "a" * 5000
    with (
        patch.object(
            web_search,
            "_search_wikipedia",
            return_value=[
                {"source": "wikipedia", "title": "X", "summary": long_summary, "url": "http://x"}
            ],
        ),
        patch.object(web_search, "_search_duckduckgo", return_value=[]),
    ):
        out = web_search.search_story_web("t")
    assert len(out["combined_summary"]) <= 4010
    assert out["combined_summary"].endswith("...")


def test_search_story_web_drops_ddg_with_empty_summary():
    with (
        patch.object(web_search, "_search_wikipedia", return_value=[]),
        patch.object(
            web_search,
            "_search_duckduckgo",
            return_value=[
                {"source": "duckduckgo", "title": "A", "summary": "   ", "url": "http://x"},
            ],
        ),
    ):
        out = web_search.search_story_web("t")
    # Empty/whitespace summaries excluded from combined
    assert "A" not in out["combined_summary"]


class TestWebSearchUncovered:
    def test_filter_sections_neutral_section_loop_and_empty_clean(self):
        """Test _filter_sections lines 135-139 and line 152."""
        # 1. Neutral section header followed by another header (triggers line 137 inside loop)
        text = "=Other Section=\nThe hero lives here.\n=Plot=\nThe plot continues."
        out = web_search._filter_sections(text)
        assert "hero lives here" in out
        assert "plot continues" not in out

        # 2. Last section is neutral but all lines are spoilers, so clean is empty (triggers line 152 falsy check)
        text_spoiler = "=Other Section=\nHe dies in the final battle."
        out_spoiler = web_search._filter_sections(text_spoiler)
        assert out_spoiler == ""

    def test_wiki_api_request_http_error(self):
        """Test _wiki_api_request raising HTTPError."""
        import requests

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("404 Not Found")

        with patch("requests.get", return_value=mock_response):
            with pytest.raises(requests.exceptions.HTTPError):
                web_search._wiki_api_request({"action": "query"})

    def test_search_wikipedia_missing_title_or_pageid(self):
        """Test _search_wikipedia when search results are missing title or pageid."""
        # Query returns elements with missing title or pageid
        fake_search = {
            "query": {
                "search": [
                    {"title": "", "pageid": 1},
                    {"title": "Valid", "pageid": None},
                ]
            }
        }
        with patch.object(web_search, "_wiki_api_request", return_value=fake_search):
            out = web_search._search_wikipedia("test query")
            assert out == []

    def test_search_duckduckgo_http_error_other_than_403(self):
        """Test _search_duckduckgo when openurl raises HTTPError other than 403."""
        import urllib.error

        err = urllib.error.HTTPError("http://x", 500, "Internal Server Error", {}, None)
        with patch("urllib.request.urlopen", side_effect=err):
            out = web_search._search_duckduckgo("test")
            assert out == []

    def test_search_duckduckgo_empty_snippet(self):
        """Test _search_duckduckgo when result snippet is empty."""
        html = """
        <div class="result">
            <a class="result__a" href="http://example.com">Title</a>
            <a class="result__url" href="http://example.com">example.com</a>
            <div class="result__snippet"></div>
        </div>
        """
        with patch("urllib.request.urlopen") as uo:
            uo.return_value.__enter__.return_value.read.return_value = html.encode()
            out = web_search._search_duckduckgo("test")
            # Should skip because snippet is empty
            assert out == []

    def test_search_story_web_futures_exception(self):
        """Test ThreadPoolExecutor futures exception handling in search_story_web."""
        # Force futures to raise exception when calling result()
        mock_future = MagicMock()
        mock_future.result.side_effect = Exception("Future failed")

        with patch("concurrent.futures.ThreadPoolExecutor") as mock_executor:
            mock_executor.return_value.__enter__.return_value.submit.return_value = mock_future
            out = web_search.search_story_web("test")
            assert out["wikipedia_results"] == []
            assert out["ddg_results"] == []

    def test_search_story_web_dedup_title_summary_fallback(self):
        """Test DDG dedup with empty url and (title, summary) tuple matches."""
        r1 = {"source": "duckduckgo", "title": "A", "summary": "s", "url": ""}
        r2 = {"source": "duckduckgo", "title": "A", "summary": "s", "url": ""}
        r3 = {"source": "duckduckgo", "title": "B", "summary": "s", "url": ""}

        with (
            patch.object(web_search, "_search_wikipedia", return_value=[]),
            patch.object(web_search, "_search_duckduckgo", return_value=[r1, r2, r3]),
        ):
            out = web_search.search_story_web("t")
            # Should have exactly 2 results (r1/r2 are deduped, r3 is kept)
            assert len(out["ddg_results"]) == 2
