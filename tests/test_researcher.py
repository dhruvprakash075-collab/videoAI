"""Tests for utils/researcher.py

Covers:
  - ResearchItem dataclass
  - _fetch_wikipedia_rest (mocked requests): titles + summaries, error, parse
  - _fetch_wikimedia_rest (mocked requests): opensearch parse, error
  - _fetch_rss (mocked feedparser): entries, query filter, missing deps
  - _score: word overlap, edge cases
  - research_topic dispatcher: budget, ordering, disabled, no sources
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from utils.researcher import (
    DEFAULT_USER_AGENT,
    ResearchItem,
    _fetch_rss,
    _fetch_wikimedia_rest,
    _fetch_wikipedia_rest,
    _score,
    research_topic,
)

# ── ResearchItem dataclass ─────────────────────────────────────────────────


class TestResearchItem:
    def test_defaults(self):
        item = ResearchItem(title="t", text="x", url="u", source_type="wikipedia")
        assert item.title == "t"
        assert item.text == "x"
        assert item.url == "u"
        assert item.source_type == "wikipedia"
        assert item.relevance_score == 0.0


# ── _score ──────────────────────────────────────────────────────────────────


class TestScore:
    def test_no_query_words(self):
        assert _score("some text", "a be I") == 0.0

    def test_no_overlap(self):
        assert _score("hello world", "completely unrelated query") == 0.0

    def test_full_overlap(self):
        assert _score("the amazon river is wide", "the amazon river") == 1.0

    def test_partial_overlap(self):
        s = _score("foo bar baz", "foo qux")
        assert 0.0 < s < 1.0

    def test_case_insensitive(self):
        assert _score("The Amazon", "the amazon") == 1.0


# ── _fetch_wikipedia_rest ───────────────────────────────────────────────────


def _requests_mock(json_data, status_code=200, raise_exc=False):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data
    if raise_exc:
        mock.raise_for_status.side_effect = Exception("boom")
    return mock


class TestFetchWikipedia:
    def test_returns_items(self):
        search_resp = _requests_mock(
            [
                "query",
                ["Amazon River", "Amazon rainforest"],
                ["", ""],
                [
                    "https://en.wikipedia.org/wiki/Amazon_River",
                    "https://en.wikipedia.org/wiki/Amazon_rainforest",
                ],
            ]
        )
        summary_data = {
            "extract": "The Amazon River is the largest river by discharge.",
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Amazon_River"}},
        }
        summary_resp = _requests_mock(summary_data)
        config = {"research": {"user_agent": "TestAgent", "timeout_s": 5, "per_source_limit": 3}}
        with patch(
            "requests.get", side_effect=[search_resp, summary_resp, summary_resp]
        ) as mock_get:
            items = _fetch_wikipedia_rest("amazon", config)
        assert len(items) == 2
        assert items[0].title == "Amazon River"
        assert items[0].source_type == "wikipedia"
        assert "Amazon" in items[0].text
        assert mock_get.call_count == 3

    def test_http_error_returns_empty(self):
        config = {"research": {"timeout_s": 5, "per_source_limit": 3}}
        with patch("requests.get", side_effect=Exception("network down")):
            assert _fetch_wikipedia_rest("amazon", config) == []

    def test_malformed_json_returns_empty(self):
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.raise_for_status.return_value = None
        search_resp.json.side_effect = ValueError("not json")
        config = {"research": {"timeout_s": 5, "per_source_limit": 3}}
        with patch("requests.get", return_value=search_resp):
            assert _fetch_wikipedia_rest("amazon", config) == []

    def test_summary_failure_skipped(self):
        search_resp = _requests_mock(
            ["q", ["Amazon"], [""], ["https://en.wikipedia.org/wiki/Amazon"]]
        )
        bad_summary = MagicMock()
        bad_summary.raise_for_status.side_effect = Exception("404")
        config = {"research": {"timeout_s": 5, "per_source_limit": 3}}
        with patch("requests.get", side_effect=[search_resp, bad_summary]):
            items = _fetch_wikipedia_rest("amazon", config)
        assert items == []

    def test_empty_text_skipped(self):
        search_resp = _requests_mock(
            ["q", ["Amazon"], [""], ["https://en.wikipedia.org/wiki/Amazon"]]
        )
        summary_resp = _requests_mock({"extract": ""})
        config = {"research": {"timeout_s": 5, "per_source_limit": 3}}
        with patch("requests.get", side_effect=[search_resp, summary_resp]):
            assert _fetch_wikipedia_rest("amazon", config) == []


# ── _fetch_wikimedia_rest ───────────────────────────────────────────────────


class TestFetchWikimedia:
    def test_returns_items(self):
        resp = _requests_mock(
            [
                "amazon",
                ["Amazon River", "Amazon basin"],
                ["", ""],
                [
                    "https://en.wikipedia.org/wiki/Amazon_River",
                    "https://en.wikipedia.org/wiki/Amazon_basin",
                ],
            ]
        )
        config = {"research": {"user_agent": "TestAgent", "timeout_s": 5, "per_source_limit": 3}}
        with patch("requests.get", return_value=resp):
            items = _fetch_wikimedia_rest("amazon", config)
        assert len(items) == 2
        assert items[0].title == "Amazon River"
        assert items[0].source_type == "wikimedia"
        assert items[0].url == "https://en.wikipedia.org/wiki/Amazon_River"

    def test_error_returns_empty(self):
        config = {"research": {"timeout_s": 5, "per_source_limit": 3}}
        with patch("requests.get", side_effect=Exception("boom")):
            assert _fetch_wikimedia_rest("amazon", config) == []

    def test_malformed_returns_empty(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status.return_value = None
        resp.json.side_effect = ValueError("bad json")
        config = {"research": {"timeout_s": 5, "per_source_limit": 3}}
        with patch("requests.get", return_value=resp):
            assert _fetch_wikimedia_rest("amazon", config) == []


# ── _fetch_rss ──────────────────────────────────────────────────────────────


class FakeFeedEntry:
    def __init__(self, title, summary, link):
        self.title = title
        self.summary = summary
        self.description = summary
        self.link = link


class FakeFeed:
    def __init__(self, entries, bozo=False):
        self.entries = entries
        self.bozo = bozo


class TestFetchRss:
    def test_returns_matching_items(self):
        entries = [
            FakeFeedEntry(
                "Amazon discovery", "The Amazon river is huge", "https://news.example.com/a1"
            ),
            FakeFeedEntry(
                "Unrelated topic", "Nothing to do with query", "https://news.example.com/u1"
            ),
            FakeFeedEntry(
                "Amazon drought", "Drought in Amazon basin", "https://news.example.com/a2"
            ),
        ]
        fake_feedparser = MagicMock()
        fake_feedparser.parse.return_value = FakeFeed(entries)
        config = {
            "research": {
                "user_agent": "TestAgent",
                "timeout_s": 5,
                "per_source_limit": 3,
                "rss_urls": ["https://news.example.com/rss"],
            }
        }
        with patch.dict("sys.modules", {"feedparser": fake_feedparser}):
            items = _fetch_rss("amazon", config)
        assert len(items) == 2
        assert all(item.source_type == "rss" for item in items)
        assert "Amazon" in items[0].title or "Amazon" in items[0].text

    def test_no_feeds_returns_empty(self):
        config = {"research": {"rss_urls": [], "per_source_limit": 3}}
        with patch.dict("sys.modules", {"feedparser": MagicMock()}):
            assert _fetch_rss("amazon", config) == []

    def test_bozo_with_no_entries_skips(self):
        fake_feedparser = MagicMock()
        fake_feedparser.parse.return_value = FakeFeed([], bozo=True)
        config = {"research": {"rss_urls": ["https://x.example.com/rss"], "per_source_limit": 3}}
        with patch.dict("sys.modules", {"feedparser": fake_feedparser}):
            assert _fetch_rss("amazon", config) == []

    def test_query_filter_excludes_non_matching(self):
        entries = [
            FakeFeedEntry("Sports update", "Football scores", "https://x.example.com/s1"),
        ]
        fake_feedparser = MagicMock()
        fake_feedparser.parse.return_value = FakeFeed(entries)
        config = {"research": {"rss_urls": ["https://x.example.com/rss"], "per_source_limit": 3}}
        with patch.dict("sys.modules", {"feedparser": fake_feedparser}):
            assert _fetch_rss("amazon", config) == []

    def test_per_source_limit_caps_results(self):
        entries = [
            FakeFeedEntry(f"Amazon news {i}", f"Amazon story {i}", f"https://x.example.com/{i}")
            for i in range(10)
        ]
        fake_feedparser = MagicMock()
        fake_feedparser.parse.return_value = FakeFeed(entries)
        config = {"research": {"rss_urls": ["https://x.example.com/rss"], "per_source_limit": 2}}
        with patch.dict("sys.modules", {"feedparser": fake_feedparser}):
            items = _fetch_rss("amazon", config)
        assert len(items) == 2


# ── research_topic dispatcher ──────────────────────────────────────────────


class TestResearchTopic:
    def test_empty_query_returns_empty(self):
        assert research_topic("", {}) == []
        assert research_topic("   ", {}) == []

    def test_disabled_returns_empty(self):
        config = {"research": {"enabled": False, "sources": ["wikipedia"]}}
        assert research_topic("amazon", config) == []

    def test_budget_zero_returns_empty(self):
        config = {"research": {"enabled": True, "budget": 0, "sources": ["wikipedia"]}}
        assert research_topic("amazon", config) == []

    def test_single_source_exhausts_budget(self):
        wiki_data = ["q", ["Amazon River"], [""], ["https://en.wikipedia.org/wiki/Amazon_River"]]
        wiki_summary = {
            "extract": "Amazon river",
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Amazon_River"}},
        }
        search_resp = _requests_mock(wiki_data)
        summary_resp = _requests_mock(wiki_summary)
        config = {
            "research": {
                "enabled": True,
                "sources": ["wikipedia"],
                "budget": 1,
                "timeout_s": 5,
                "per_source_limit": 3,
            }
        }
        with patch("requests.get", side_effect=[search_resp, summary_resp]) as mock_get:
            items = research_topic("amazon", config)
        assert len(items) == 1
        assert items[0].source_type == "wikipedia"
        assert mock_get.call_count == 2

    def test_budget_limits_source_count(self):
        wiki_data = ["q", ["Amazon River"], [""], ["https://en.wikipedia.org/wiki/Amazon_River"]]
        wiki_summary = {
            "extract": "Amazon river text",
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Amazon_River"}},
        }
        wiki_search = _requests_mock(wiki_data)
        wiki_summary_resp = _requests_mock(wiki_summary)
        wm_data = ["q", ["Amazon basin"], [""], ["https://en.wikipedia.org/wiki/Amazon_basin"]]
        wm_resp = _requests_mock(wm_data)
        fake_fdp = MagicMock()
        fake_fdp.parse.return_value = FakeFeed(
            [FakeFeedEntry("Amazon news", "Amazon story", "https://x.example.com/a")]
        )
        config = {
            "research": {
                "enabled": True,
                "sources": ["wikipedia", "wikimedia", "rss"],
                "budget": 2,
                "timeout_s": 5,
                "per_source_limit": 3,
                "rss_urls": ["https://x.example.com/rss"],
                "user_agent": "TestAgent",
            }
        }
        with (
            patch(
                "requests.get", side_effect=[wiki_search, wiki_summary_resp, wm_resp]
            ) as mock_get,
            patch.dict("sys.modules", {"feedparser": fake_fdp}),
        ):
            items = research_topic("amazon", config)
        assert mock_get.call_count == 3
        wiki_titles = [it for it in items if it.source_type == "wikipedia"]
        wm_titles = [it for it in items if it.source_type == "wikimedia"]
        rss_titles = [it for it in items if it.source_type == "rss"]
        assert len(wiki_titles) == 1
        assert len(wm_titles) == 1
        assert len(rss_titles) == 0

    def test_results_sorted_by_score(self):
        wiki_summary_high = {
            "extract": "Amazon Amazon Amazon river water",
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Amazon"}},
        }
        wiki_search = _requests_mock(
            ["q", ["Amazon"], [""], ["https://en.wikipedia.org/wiki/Amazon"]]
        )
        wiki_summary_resp = _requests_mock(wiki_summary_high)
        wm_data = ["q", ["Other thing"], [""], ["https://en.wikipedia.org/wiki/Other"]]
        wm_resp = _requests_mock(wm_data)
        config = {
            "research": {
                "enabled": True,
                "sources": ["wikipedia", "wikimedia"],
                "budget": 3,
                "timeout_s": 5,
                "per_source_limit": 3,
            }
        }
        with patch("requests.get", side_effect=[wiki_search, wiki_summary_resp, wm_resp]):
            items = research_topic("amazon river", config)
        assert items[0].source_type == "wikipedia"
        assert items[0].relevance_score >= items[-1].relevance_score

    def test_dedup_by_url(self):
        wiki_data = ["q", ["Amazon"], [""], ["https://en.wikipedia.org/wiki/Amazon"]]
        wiki_summary = {
            "extract": "Amazon river",
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Amazon"}},
        }
        wiki_search = _requests_mock(wiki_data)
        wiki_summary_resp = _requests_mock(wiki_summary)
        wm_data = ["q", ["Amazon"], [""], ["https://en.wikipedia.org/wiki/Amazon"]]
        wm_resp = _requests_mock(wm_data)
        config = {
            "research": {
                "enabled": True,
                "sources": ["wikipedia", "wikimedia"],
                "budget": 3,
                "timeout_s": 5,
                "per_source_limit": 3,
            }
        }
        with patch("requests.get", side_effect=[wiki_search, wiki_summary_resp, wm_resp]):
            items = research_topic("amazon", config)
        assert len(items) == 1

    def test_source_failure_isolated(self):
        wiki_data = ["q", ["Amazon"], [""], ["https://en.wikipedia.org/wiki/Amazon"]]
        wiki_summary = {
            "extract": "Amazon river text",
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Amazon"}},
        }
        wiki_search = _requests_mock(wiki_data)
        wiki_summary_resp = _requests_mock(wiki_summary)
        config = {
            "research": {
                "enabled": True,
                "sources": ["wikipedia", "wikimedia"],
                "budget": 3,
                "timeout_s": 5,
                "per_source_limit": 3,
            }
        }
        with patch(
            "requests.get", side_effect=[wiki_search, wiki_summary_resp, Exception("WM down")]
        ):
            items = research_topic("amazon", config)
        assert len(items) == 1
        assert items[0].source_type == "wikipedia"

    def test_unknown_source_skipped(self):
        config = {
            "research": {
                "enabled": True,
                "sources": ["nonsense_source"],
                "budget": 3,
                "timeout_s": 5,
                "per_source_limit": 3,
            }
        }
        assert research_topic("amazon", config) == []

    def test_default_config_uses_all_sources(self):
        with patch("requests.get", side_effect=Exception("network down")):
            items = research_topic("amazon", {})
        assert items == []

    def test_user_agent_default(self):
        from contextlib import suppress

        with patch("requests.get", side_effect=Exception("boom")) as mock_get:
            with suppress(Exception):
                research_topic(
                    "amazon",
                    {
                        "research": {
                            "enabled": True,
                            "sources": ["wikimedia"],
                            "budget": 1,
                            "per_source_limit": 1,
                        }
                    },
                )
        assert mock_get.called
        call_kwargs = mock_get.call_args.kwargs
        assert call_kwargs.get("headers", {}).get("User-Agent") == DEFAULT_USER_AGENT


# ── End-to-end smoke ────────────────────────────────────────────────────────


class TestEndToEnd:
    def test_full_flow_with_mocks(self):
        wiki_data = [
            "q",
            ["Amazon River", "Amazon basin"],
            [""],
            [
                "https://en.wikipedia.org/wiki/Amazon_River",
                "https://en.wikipedia.org/wiki/Amazon_basin",
            ],
        ]
        wiki_summary_1 = {
            "extract": "The Amazon River flows through South America.",
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Amazon_River"}},
        }
        wiki_summary_2 = {
            "extract": "The Amazon basin is the largest drainage basin.",
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Amazon_basin"}},
        }
        wiki_search = _requests_mock(wiki_data)
        wiki_summary_resp_1 = _requests_mock(wiki_summary_1)
        wiki_summary_resp_2 = _requests_mock(wiki_summary_2)
        wm_data = ["q", ["Amazon (novel)"], [""], ["https://en.wikipedia.org/wiki/Amazon_(novel)"]]
        wm_resp = _requests_mock(wm_data)
        rss_entries = [
            FakeFeedEntry(
                "Amazon expedition", "Scientists explore the Amazon", "https://news.example.com/exp"
            )
        ]
        fake_fdp = MagicMock()
        fake_fdp.parse.return_value = FakeFeed(rss_entries)
        config = {
            "research": {
                "enabled": True,
                "sources": ["wikipedia", "wikimedia", "rss"],
                "budget": 3,
                "timeout_s": 5,
                "per_source_limit": 3,
                "rss_urls": ["https://news.example.com/rss"],
                "user_agent": "VideoAI-Test",
            }
        }
        with (
            patch(
                "requests.get",
                side_effect=[wiki_search, wiki_summary_resp_1, wiki_summary_resp_2, wm_resp],
            ) as mock_get,
            patch.dict("sys.modules", {"feedparser": fake_fdp}),
        ):
            items = research_topic("amazon", config)
        assert len(items) == 4
        assert mock_get.call_count == 4
        assert {it.source_type for it in items} == {"wikipedia", "wikimedia", "rss"}
