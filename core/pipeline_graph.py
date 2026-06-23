"""pipeline_graph.py - LangGraph Node Architecture for Video.AI segment processing."""

import logging
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

log = logging.getLogger(__name__)


class SegmentState(TypedDict, total=False):
    """The typed state passed between nodes in the LangGraph."""

    i: int
    plan: dict
    context: str

    # Source path (Phase 4): when set, the writer short-circuits to the
    # chunk text and the critic auto-approves (verbatim source needs no rubric).
    source_chunk: Any

    # Script Node
    script: str
    rewrites_attempted: int

    # Critic Node
    critic_approved: bool
    critic_feedback: str

    # Translation Node
    devanagari_script: str
    script_for_tts: str

    # Audio Node
    audio_path: str
    word_timestamps_json: str

    # Image Node
    images: list[str]

    # Render Node
    mp4_path: str

    # Memory Review
    memory_items: list[dict]

    # Performance caching fields
    enriched_prompts: list
    memory_data: dict

    # Control signals
    aborted: bool
    skip: bool


class SegmentGraphBuilder:
    def __init__(self, ctx: Any):
        """ctx is an object holding all dependencies (config, topic, scheduler, etc.)"""
        self.ctx = ctx

    def write_script_node(self, state: SegmentState) -> dict:
        if state.get("aborted") or state.get("skip"):
            return {}
        # Delegate to the context implementation
        return self.ctx.do_write_script(state)

    def critic_node(self, state: SegmentState) -> dict:
        if state.get("aborted") or state.get("skip"):
            return {}
        return self.ctx.do_critic(state)

    def translate_node(self, state: SegmentState) -> dict:
        if state.get("aborted") or state.get("skip"):
            return {}
        return self.ctx.do_translate(state)

    def tts_node(self, state: SegmentState) -> dict:
        if state.get("aborted") or state.get("skip"):
            return {}
        return self.ctx.do_tts(state)

    def image_node(self, state: SegmentState) -> dict:
        if state.get("aborted") or state.get("skip"):
            return {}
        return self.ctx.do_image_gen(state)

    def important_image_review_node(self, state: SegmentState) -> dict:
        if state.get("aborted") or state.get("skip"):
            return {}
        return self.ctx.do_important_image_review(state)

    def render_node(self, state: SegmentState) -> dict:
        if state.get("aborted") or state.get("skip"):
            return {}
        return self.ctx.do_render(state)

    def memory_review_node(self, state: SegmentState) -> dict:
        if state.get("aborted") or state.get("skip"):
            return {}
        return self.ctx.do_memory_review(state)

    def route_after_critic(self, state: SegmentState) -> str:
        if state.get("aborted") or state.get("skip"):
            return END
        if not state.get("critic_approved", True):
            rewrites = state.get("rewrites_attempted", 0)
            max_rewrites = self.ctx.config.get("critic", {}).get("max_rewrites", 2)
            if rewrites < max_rewrites:
                log.info(
                    f"  Seg {state['i']}: Critic rejected script. Routing back to writer (attempt {rewrites + 1}/{max_rewrites})."
                )
                return "write_script_node"
            else:
                log.warning(
                    f"  Seg {state['i']}: Max rewrites reached. Proceeding with unapproved script."
                )
                return "translate_node"
        return "translate_node"

    def route_after_write(self, state: SegmentState) -> str:
        if state.get("aborted") or state.get("skip"):
            return END
        # The critic is the single source of truth for whether scripts are
        # reviewed. When critic.enabled is false, skip the critic node and
        # route the writer's output straight to translation.
        if not self.ctx.config.get("critic", {}).get("enabled", True):
            return "translate_node"
        return "critic_node"

    def build(self) -> Any:
        builder = StateGraph(SegmentState)

        builder.add_node("write_script_node", self.write_script_node)
        builder.add_node("critic_node", self.critic_node)
        builder.add_node("translate_node", self.translate_node)
        builder.add_node("tts_node", self.tts_node)
        builder.add_node("image_node", self.image_node)
        builder.add_node("important_image_review_node", self.important_image_review_node)
        builder.add_node("render_node", self.render_node)
        builder.add_node("memory_review_node", self.memory_review_node)

        builder.set_entry_point("write_script_node")

        builder.add_conditional_edges(
            "critic_node",
            self.route_after_critic,
            {
                "write_script_node": "write_script_node",
                "translate_node": "translate_node",
                END: END,
            },
        )

        builder.add_conditional_edges(
            "write_script_node",
            self.route_after_write,
            {
                "critic_node": "critic_node",
                "translate_node": "translate_node",
                END: END,
            },
        )
        builder.add_edge("translate_node", "tts_node")
        builder.add_edge("tts_node", "image_node")
        builder.add_edge("image_node", "important_image_review_node")
        builder.add_edge("important_image_review_node", "render_node")
        builder.add_edge("render_node", "memory_review_node")
        builder.add_edge("memory_review_node", END)

        return builder.compile()
