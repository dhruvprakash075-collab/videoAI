"""context_manager.py - Ollama Context Window Manager.

Prevents LLM context overflow on long runs (10+ segments) by maintaining
a token budget and compressing older context entries when the window fills.

Design:
  - Always keeps the last 2 segment summaries verbatim (most relevant)
  - Fills remaining budget with older summaries, newest-first
  - When budget is exhausted, replaces oldest entries with a single
    LLM-generated rolling summary (called only when overflow occurs)
  - World state block is ALWAYS included with highest priority

Token estimation:
  Uses fast whitespace-based approximation: len(text.split()) * 1.35
  Good enough for budget management without tokenizer overhead.
"""

import logging

log = logging.getLogger(__name__)

# Overhead budget reserved for system prompt, plan, and world state
_SYSTEM_OVERHEAD_TOKENS = 1500
# Max tokens for the full context block passed to the writer LLM
_DEFAULT_BUDGET = 6000


class ContextWindowManager:
    """Manages the context budget for LLM segment script generation.

    Usage:
        ctx_mgr = ContextWindowManager()
        context_str = ctx_mgr.build_context_for_prompt(
            memory_entries  = mem.get_all_entries(topic),
            world_state_block = world_state.to_prompt_block(),
        )
        # Pass context_str to build_segment_prompt()
    """

    def __init__(self, budget_tokens: int = _DEFAULT_BUDGET):
        self.budget_tokens = budget_tokens
        self._rolling_summary: str | None = None  # cached LLM compression
        self._summary_covers_up_to: int = 0  # segment index covered by summary

    # ── token estimation ─────────────────────────────────────────────────────

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Fast whitespace-based token count approximation."""
        if not text:
            return 0
        return max(1, int(len(text.split()) * 1.35))

    # ── context building ─────────────────────────────────────────────────────

    def build_context_for_prompt(
        self,
        memory_entries: list[dict],
        world_state_block: str = "",
        agent=None,
    ) -> str:
        """Build a context string that fits within the token budget.

        Args:
            memory_entries:    List of {segment, summary, script} dicts from StoryMemory.
            world_state_block: Pre-built constraint block from WorldState.to_prompt_block().
            agent:             Optional CrewAI agent for LLM compression when overflow occurs.

        Returns:
            Context string ready for injection into the writer LLM prompt.
        """
        if not memory_entries:
            ws = f"\n{world_state_block}\n" if world_state_block else ""
            return ws

        # ── Budget allocation ────────────────────────────────────────────────
        ws_tokens = self.estimate_tokens(world_state_block)
        avail = self.budget_tokens - ws_tokens - _SYSTEM_OVERHEAD_TOKENS

        # ── Always include last 2 verbatim ───────────────────────────────────
        recent = memory_entries[-2:]
        older = memory_entries[:-2]
        recent_text = self._format_entries(recent)
        used = self.estimate_tokens(recent_text)

        parts = [recent_text]

        if used > avail:
            # Even last 2 are too long — trim to summaries only
            log.warning(
                f"[CtxMgr] Recent 2 segments exceed budget ({used} > {avail} tokens) — trimming to summaries"
            )
            parts = [self._format_entries(recent, summaries_only=True)]
            used = self.estimate_tokens(parts[0])

        # ── Fill remaining budget with older entries (newest-first) ───────────
        remaining = avail - used
        older_included = []

        for idx in range(len(older) - 1, -1, -1):
            entry = older[idx]
            entry_text = self._format_entry(entry)
            cost = self.estimate_tokens(entry_text)
            if cost <= remaining:
                older_included.insert(0, entry_text)
                remaining -= cost
            else:
                # Budget exceeded — use rolling summary for everything older
                # P3-13 fix: use explicit index (idx) instead of older.index(entry)
                # to avoid dict-equality false matches on duplicate/default entries.
                compressed = self._get_or_build_rolling_summary(
                    entries=older[: idx + 1],
                    remaining_budget=remaining,
                    agent=agent,
                )
                if compressed:
                    older_included.insert(0, compressed)
                break

        # ── Assemble final context block ─────────────────────────────────────
        all_parts = []
        if world_state_block:
            all_parts.append(world_state_block)
        if older_included:
            all_parts.append("[Earlier story context — compressed]")
            all_parts.extend(older_included)
            all_parts.append("[/Earlier story context]")
        all_parts.append("[Recent story context]")
        all_parts.extend(parts)
        all_parts.append("[/Recent story context]")

        result = "\n".join(all_parts)
        total = self.estimate_tokens(result)
        log.info(
            f"[CtxMgr] Context built: ~{total} tokens "
            f"(budget {self.budget_tokens}, {len(memory_entries)} entries, "
            f"{len(older_included)} older + {len(recent)} recent)"
        )
        return result

    # ── rolling summary ───────────────────────────────────────────────────────

    def _get_or_build_rolling_summary(
        self,
        entries: list[dict],
        remaining_budget: int,
        agent=None,
    ) -> str:
        """Return a cached rolling summary, or build one via LLM if budget allows."""
        if not entries:
            return ""

        # Use cached summary if it covers these entries
        last_seg = entries[-1].get("segment", 0)
        if (
            self._rolling_summary
            and self._summary_covers_up_to >= last_seg
            and self.estimate_tokens(self._rolling_summary) <= remaining_budget
        ):
            log.debug(
                f"[CtxMgr] Using cached rolling summary (covers up to seg {self._summary_covers_up_to})"
            )
            return self._rolling_summary

        # Try LLM compression
        if agent is not None and remaining_budget > 0:
            try:
                summary = self._llm_compress(entries, agent)
                if summary and self.estimate_tokens(summary) <= remaining_budget:
                    self._rolling_summary = summary
                    self._summary_covers_up_to = last_seg
                    log.info(f"[CtxMgr] Built LLM rolling summary for segs 1-{last_seg}")
                    return summary
            except Exception as e:
                log.warning(f"[CtxMgr] LLM compression failed ({e}) — using simple summary")

        # Fallback: simple truncated summary
        simple = "; ".join(
            f"Seg {e.get('segment', '?')}: {e.get('summary', '')[:80]}" for e in entries[-3:]
        )
        fallback = f"[Earlier segments summary] {simple} [/Earlier segments summary]"
        if self.estimate_tokens(fallback) <= remaining_budget:
            return fallback

        # Last resort: just mention how many segments were skipped
        return f"[Note: {len(entries)} earlier segments omitted to fit context window]"

    def _llm_compress(self, entries: list[dict], agent) -> str:
        """Use the CrewAI director agent to compress older segments into 2 sentences.

        B15 fix: acquires the shared CrewAI serialization lock so this kickoff()
        cannot run concurrently with the pipeline's writer kickoffs (which would
        corrupt CrewAI's single-threaded executor). If the lock can't be acquired
        within a short timeout, raises so the caller falls back to the non-LLM summary.

        Task 2: routes through guarded_crewai_kickoff() so the per-model circuit
        breaker protects this path. A breaker-open immediately returns the
        non-LLM summary, preventing a hung LLM from blocking the pipeline.
        """
        from crewai import Crew, Task
        from crewai.process import Process

        from utils.crewai_breaker import BreakerOpen, guarded_crewai_kickoff

        segs_text = "\n".join(
            f"Segment {e.get('segment', '?')}: {e.get('summary', '')}" for e in entries
        )
        compress_prompt = (
            "Summarize the following story segments in exactly 2 sentences. "
            "Preserve key character names, world facts, and unresolved plot threads. "
            "Plain text only, no markdown.\n\n"
            f"{segs_text}"
        )
        crew = Crew(
            agents=[agent],
            tasks=[
                Task(
                    description=compress_prompt,
                    agent=agent,
                    expected_output="2-sentence story summary",
                )
            ],
            process=Process.sequential,
        )

        # B15 fix: serialize through the shared CrewAI lock with a timeout
        # P3-14 fix: also wrap in global_scheduler.task("heavy") so the LLM
        # compression kickoff is subject to VRAM scheduling like all other heavy work.
        try:
            from utils.concurrency import crewai_lock, global_scheduler as _sched
        except Exception:
            crewai_lock = None
            _sched = None

        model_name = str(getattr(getattr(agent, "llm", None), "model", "context-compress"))

        def _do_kickoff():
            if crewai_lock is not None:
                if not crewai_lock.acquire(timeout=30):
                    raise RuntimeError("CrewAI lock timeout — fall back to non-LLM summary")
                try:
                    if _sched is not None:
                        with _sched.task("heavy", "context-compress"):
                            return guarded_crewai_kickoff(
                                crew, model_name=model_name, timeout_s=120.0
                            )
                    return guarded_crewai_kickoff(crew, model_name=model_name, timeout_s=120.0)
                finally:
                    crewai_lock.release()
            return guarded_crewai_kickoff(crew, model_name=model_name, timeout_s=120.0)

        try:
            result = _do_kickoff()
        except BreakerOpen as _bo:
            log.warning(f"Context-compress breaker open — falling back to non-LLM summary: {_bo}")
            raise
        except Exception as e:
            log.warning(f"Context-compress LLM call failed: {e}")
            raise

        raw = result.raw if hasattr(result, "raw") else str(result)
        return f"[Story so far] {raw.strip()} [/Story so far]"

    # ── formatting helpers ────────────────────────────────────────────────────

    @staticmethod
    def _format_entry(entry: dict, summaries_only: bool = False) -> str:
        seg = entry.get("segment", "?")
        if summaries_only:
            return f"Segment {seg}: {entry.get('summary', '')}"
        summary = entry.get("summary", "")
        script = entry.get("script", "")
        if script and len(script) > 300:
            script = script[:300] + "..."
        return f"Segment {seg}: {summary}\n  Script excerpt: {script}"

    @classmethod
    def _format_entries(cls, entries: list[dict], summaries_only: bool = False) -> str:
        return "\n".join(cls._format_entry(e, summaries_only) for e in entries)
