"""test_context_manager.py - ContextWindowManager token budget + compression."""

from utils.context_manager import ContextWindowManager


def test_estimate_tokens_empty():
    assert ContextWindowManager.estimate_tokens("") == 0
    assert ContextWindowManager.estimate_tokens(None) == 0


def test_estimate_tokens_basic():
    # 4 words * 1.35 = 5.4 -> int -> 5
    assert ContextWindowManager.estimate_tokens("one two three four") >= 5


def test_estimate_tokens_minimum_one():
    assert ContextWindowManager.estimate_tokens("x") == 1


def test_no_entries_returns_world_state_only():
    mgr = ContextWindowManager()
    out = mgr.build_context_for_prompt(memory_entries=[], world_state_block="WS_BLOCK")
    assert "WS_BLOCK" in out
    assert "[Recent story context]" not in out


def test_no_entries_no_world_state():
    mgr = ContextWindowManager()
    out = mgr.build_context_for_prompt(memory_entries=[])
    assert out == ""


def test_recent_two_always_included():
    mgr = ContextWindowManager(budget_tokens=10000)
    entries = [
        {"segment": i, "summary": f"summary {i}", "script": f"script {i}"} for i in range(1, 6)
    ]
    out = mgr.build_context_for_prompt(memory_entries=entries)
    assert "Segment 5" in out
    assert "Segment 4" in out


def test_older_entries_included_within_budget():
    mgr = ContextWindowManager(budget_tokens=100000)
    entries = [
        {"segment": i, "summary": f"summary {i}", "script": f"script {i} " * 5}
        for i in range(1, 11)
    ]
    out = mgr.build_context_for_prompt(memory_entries=entries)
    assert "Segment 1" in out
    assert "Segment 10" in out


def test_recent_exceeds_budget_trims_to_summaries():
    mgr = ContextWindowManager(budget_tokens=10)  # tiny budget
    entries = [
        {"segment": 1, "summary": "A" * 1000, "script": "B" * 1000},
        {"segment": 2, "summary": "C" * 1000, "script": "D" * 1000},
    ]
    out = mgr.build_context_for_prompt(memory_entries=entries)
    # Should still produce a non-empty result
    assert "Segment" in out


def test_older_compressed_when_budget_exceeded():
    mgr = ContextWindowManager(budget_tokens=200)  # very small
    entries = [
        {"segment": i, "summary": f"summary {i}", "script": f"script {i} " * 50}
        for i in range(1, 20)
    ]
    out = mgr.build_context_for_prompt(memory_entries=entries)
    # Should mention either the summary or compression
    assert "context" in out.lower() or "summary" in out.lower() or "Segment" in out


def test_rolling_summary_uses_cache():
    mgr = ContextWindowManager(budget_tokens=100)
    entries = [{"segment": 1, "summary": "s1", "script": "x1"}]
    # Force the cache path by calling _get_or_build_rolling_summary
    out1 = mgr._get_or_build_rolling_summary(entries=entries, remaining_budget=500)
    # Second call should use cache
    out2 = mgr._get_or_build_rolling_summary(entries=entries, remaining_budget=500)
    assert out1 == out2


def test_rolling_summary_falls_back_on_no_agent():
    mgr = ContextWindowManager()
    entries = [{"segment": 1, "summary": "abc", "script": "def"}]
    out = mgr._get_or_build_rolling_summary(entries=entries, remaining_budget=500)
    # Simple fallback format
    assert "Seg 1" in out or "summary" in out.lower()


def test_rolling_summary_handles_no_entries():
    mgr = ContextWindowManager()
    out = mgr._get_or_build_rolling_summary(entries=[], remaining_budget=100)
    assert out == ""


def test_format_entry_summaries_only():
    e = {"segment": 5, "summary": "hello", "script": "world"}
    out = ContextWindowManager._format_entry(e, summaries_only=True)
    assert out == "Segment 5: hello"
    assert "world" not in out


def test_format_entry_truncates_long_script():
    e = {"segment": 1, "summary": "s", "script": "x" * 1000}
    out = ContextWindowManager._format_entry(e)
    assert "..." in out
    assert len(out) < 1000


def test_format_entry_handles_missing_fields():
    e = {"segment": 1}
    out = ContextWindowManager._format_entry(e)
    assert "Segment 1" in out


def test_format_entries_joins_with_newlines():
    entries = [
        {"segment": 1, "summary": "a"},
        {"segment": 2, "summary": "b"},
    ]
    out = ContextWindowManager._format_entries(entries)
    assert "Segment 1" in out
    assert "Segment 2" in out
    assert "\n" in out


def test_rolling_summary_simple_fallback_path():
    """When no agent and budget tiny, returns last-resort note."""
    mgr = ContextWindowManager()
    # No cache populated
    entries = [{"segment": i, "summary": "x" * 100, "script": ""} for i in range(1, 20)]
    # remaining_budget=1 forces fallback path
    out = mgr._get_or_build_rolling_summary(entries=entries, remaining_budget=1)
    # Could be either simple or last-resort â€” just non-empty
    assert isinstance(out, str)
    assert len(out) > 0


# â”€â”€ _llm_compress â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestLlmCompress:
    """Tests for ContextWindowManager._llm_compress (lines 188-262).

    Note: Crew/Task/guarded_crewai_kickoff are imported INSIDE _llm_compress,
    so we patch at their source modules: crewai.Crew, crewai.Task, etc.
    crewai_lock and global_scheduler are also local imports from utils.concurrency,
    so we patch at utils.concurrency.crewai_lock / utils.concurrency.global_scheduler.
    """

    def _make_mgr(self):
        return ContextWindowManager()

    def test_llm_compress_success_with_raw_attr(self):
        """LLM compress returns [Story so far] wrapper around result.raw."""
        from unittest.mock import MagicMock, patch

        mgr = self._make_mgr()
        agent = MagicMock()
        agent.llm.model = "hermes"

        mock_result = MagicMock()
        mock_result.raw = "  The hero slew the dragon. All was well.  "

        entries = [{"segment": 1, "summary": "Hero fights dragon"}]

        with (
            patch("crewai.Crew") as MockCrew,
            patch("crewai.Task"),
            patch("utils.crewai_breaker.guarded_crewai_kickoff", return_value=mock_result),
            patch("utils.concurrency.crewai_lock", None),
        ):
            MockCrew.return_value = MagicMock()
            result = mgr._llm_compress(entries, agent)

        assert "[Story so far]" in result
        assert "slew the dragon" in result

    def test_llm_compress_success_no_raw_attr(self):
        """When result has no .raw attribute, str(result) is used."""
        from unittest.mock import MagicMock, patch

        mgr = self._make_mgr()
        agent = MagicMock()
        agent.llm.model = "hermes"

        class NoRaw:
            def __str__(self):
                return "No-raw result text"

        mock_result = NoRaw()
        entries = [{"segment": 1, "summary": "Hero fights dragon"}]

        with (
            patch("crewai.Crew") as MockCrew,
            patch("crewai.Task"),
            patch("utils.crewai_breaker.guarded_crewai_kickoff", return_value=mock_result),
            patch("utils.concurrency.crewai_lock", None),
        ):
            MockCrew.return_value = MagicMock()
            result = mgr._llm_compress(entries, agent)

        assert "No-raw result text" in result

    def test_llm_compress_breaker_open_re_raises(self):
        """BreakerOpen propagates up from _llm_compress."""
        from unittest.mock import MagicMock, patch

        import pytest

        from utils.crewai_breaker import BreakerOpen

        mgr = self._make_mgr()
        agent = MagicMock()
        entries = [{"segment": 1, "summary": "x"}]

        with (
            patch("crewai.Crew") as MockCrew,
            patch("crewai.Task"),
            patch(
                "utils.crewai_breaker.guarded_crewai_kickoff",
                side_effect=BreakerOpen("test-model", 30.0),
            ),
            patch("utils.concurrency.crewai_lock", None),
        ):
            MockCrew.return_value = MagicMock()
            with pytest.raises(BreakerOpen):
                mgr._llm_compress(entries, agent)

    def test_llm_compress_generic_exception_re_raises(self):
        """Generic exceptions also propagate from _llm_compress."""
        from unittest.mock import MagicMock, patch

        import pytest

        mgr = self._make_mgr()
        agent = MagicMock()
        entries = [{"segment": 1, "summary": "x"}]

        with (
            patch("crewai.Crew") as MockCrew,
            patch("crewai.Task"),
            patch(
                "utils.crewai_breaker.guarded_crewai_kickoff",
                side_effect=RuntimeError("LLM timeout"),
            ),
            patch("utils.concurrency.crewai_lock", None),
        ):
            MockCrew.return_value = MagicMock()
            with pytest.raises(RuntimeError, match="LLM timeout"):
                mgr._llm_compress(entries, agent)

    def test_llm_compress_with_lock_and_scheduler(self):
        """When crewai_lock and global_scheduler are present, both are used."""
        import threading
        from contextlib import contextmanager
        from unittest.mock import MagicMock, patch

        mgr = self._make_mgr()
        agent = MagicMock()
        agent.llm.model = "hermes"

        mock_result = MagicMock()
        mock_result.raw = "Compressed."

        entries = [{"segment": 1, "summary": "x"}]

        real_lock = threading.RLock()

        class FakeScheduler:
            @contextmanager
            def task(self, _kind, _name):
                yield

        with (
            patch("crewai.Crew") as MockCrew,
            patch("crewai.Task"),
            patch("utils.crewai_breaker.guarded_crewai_kickoff", return_value=mock_result),
            patch("utils.concurrency.crewai_lock", real_lock),
            patch("utils.concurrency.global_scheduler", FakeScheduler()),
        ):
            MockCrew.return_value = MagicMock()
            result = mgr._llm_compress(entries, agent)

        assert "[Story so far]" in result

    def test_llm_compress_lock_timeout_raises(self):
        """When lock.acquire times out, RuntimeError is raised."""
        from unittest.mock import MagicMock, patch

        import pytest

        mgr = self._make_mgr()
        agent = MagicMock()
        entries = [{"segment": 1, "summary": "x"}]

        mock_lock = MagicMock()
        mock_lock.acquire.return_value = False  # timeout â†’ False

        with (
            patch("crewai.Crew") as MockCrew,
            patch("crewai.Task"),
            patch("utils.concurrency.crewai_lock", mock_lock),
        ):
            MockCrew.return_value = MagicMock()
            with pytest.raises(RuntimeError, match="CrewAI lock timeout"):
                mgr._llm_compress(entries, agent)


# â”€â”€ rolling summary cache paths â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TestRollingSummaryCachePaths:
    def test_cache_miss_due_to_covers_up_to_too_low(self):
        """Cached summary covers only older segs â†’ rebuild (no agent â†’ fallback)."""
        mgr = ContextWindowManager()
        mgr._rolling_summary = "Old cached summary"
        mgr._summary_covers_up_to = 2  # Only covers seg â‰¤ 2

        entries = [{"segment": i, "summary": f"s{i}", "script": ""} for i in range(1, 6)]
        result = mgr._get_or_build_rolling_summary(entries, remaining_budget=1000)
        assert result  # Non-empty, some form of simple fallback

    def test_cache_hit_returns_exact_cached(self):
        """Cache hit: exact cached summary returned."""
        mgr = ContextWindowManager()
        cached = "[Earlier segments summary] Seg 1: s1 [/Earlier segments summary]"
        mgr._rolling_summary = cached
        mgr._summary_covers_up_to = 3

        entries = [{"segment": i, "summary": f"s{i}", "script": ""} for i in range(1, 4)]
        result = mgr._get_or_build_rolling_summary(entries, remaining_budget=1000)
        assert result == cached

    def test_llm_compress_result_stored_in_cache(self):
        """Successful LLM compression is cached for subsequent calls."""
        from unittest.mock import MagicMock, patch

        mgr = ContextWindowManager()
        agent = MagicMock()
        agent.llm.model = "hermes"

        mock_result = MagicMock()
        mock_result.raw = "LLM produced summary."

        entries = [{"segment": i, "summary": f"s{i}", "script": ""} for i in range(1, 4)]

        with (
            patch("crewai.Crew") as MockCrew,
            patch("crewai.Task"),
            patch("utils.crewai_breaker.guarded_crewai_kickoff", return_value=mock_result),
            patch("utils.concurrency.crewai_lock", None),
        ):
            MockCrew.return_value = MagicMock()
            result = mgr._get_or_build_rolling_summary(entries, remaining_budget=1000, agent=agent)

        assert mgr._rolling_summary is not None
        assert mgr._summary_covers_up_to == 3
        assert "LLM produced summary" in result

    def test_llm_compress_result_too_large_uses_fallback(self):
        """LLM result is too large for remaining_budget â†’ falls to simple summary."""
        from unittest.mock import MagicMock, patch

        mgr = ContextWindowManager()
        agent = MagicMock()

        big_raw = " ".join(["word"] * 500)
        mock_result = MagicMock()
        mock_result.raw = big_raw

        entries = [{"segment": 1, "summary": "s1"}]

        with (
            patch("crewai.Crew") as MockCrew,
            patch("crewai.Task"),
            patch("utils.crewai_breaker.guarded_crewai_kickoff", return_value=mock_result),
            patch("utils.concurrency.crewai_lock", None),
        ):
            MockCrew.return_value = MagicMock()
            # remaining_budget=1 â†’ LLM result too large, falls to simple
            result = mgr._get_or_build_rolling_summary(entries, remaining_budget=1, agent=agent)

        assert result  # Falls through to simple/last-resort
