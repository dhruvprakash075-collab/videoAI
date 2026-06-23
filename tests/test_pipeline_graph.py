"""test_pipeline_graph.py - Regression tests for the LangGraph skeleton in
core/pipeline_graph.py. Verifies the graph builds, the routing logic is
correct, and state propagates through nodes.
"""

from core.pipeline_graph import END, SegmentGraphBuilder


class _FakeCtx:
    """Minimal context for SegmentGraphBuilder - supplies config + node fns."""

    def __init__(self, max_rewrites=2, critic_enabled=True):
        self.config = {
            "critic": {
                "enabled": critic_enabled,
                "threshold": 60,
                "max_rewrites": max_rewrites,
            },
        }

    def do_write_script(self, state):
        return {"script": "draft"}

    def do_critic(self, state):
        return {"critic_approved": True, "critic_feedback": "", "rewrites_attempted": 1}

    def do_translate(self, state):
        return {"devanagari_script": "ट्रांसलेट"}

    def do_tts(self, state):
        return {"audio_path": "/tmp/a.wav"}

    def do_image_gen(self, state):
        return {"images": ["/tmp/i.png"]}

    def do_render(self, state):
        return {"mp4_path": "/tmp/v.mp4"}


def test_graph_builds_without_error():
    builder = SegmentGraphBuilder(_FakeCtx())
    graph = builder.build()
    assert graph is not None


def test_route_after_critic_aborted_returns_end():
    builder = SegmentGraphBuilder(_FakeCtx())
    state = {"aborted": True, "critic_approved": True}
    assert builder.route_after_critic(state) == END


def test_route_after_critic_approved_returns_translate():
    builder = SegmentGraphBuilder(_FakeCtx())
    state = {"aborted": False, "critic_approved": True, "rewrites_attempted": 0}
    assert builder.route_after_critic(state) == "translate_node"


def test_route_after_critic_rejected_under_max_returns_writer():
    builder = SegmentGraphBuilder(_FakeCtx(max_rewrites=2))
    state = {"aborted": False, "critic_approved": False, "rewrites_attempted": 1, "i": 1}
    assert builder.route_after_critic(state) == "write_script_node"


def test_route_after_critic_rejected_at_max_returns_translate():
    builder = SegmentGraphBuilder(_FakeCtx(max_rewrites=2))
    state = {"aborted": False, "critic_approved": False, "rewrites_attempted": 2, "i": 1}
    assert builder.route_after_critic(state) == "translate_node"


def test_route_after_write_critic_enabled_returns_critic():
    builder = SegmentGraphBuilder(_FakeCtx())
    state = {"aborted": False, "skip": False}
    assert builder.route_after_write(state) == "critic_node"


def test_route_after_write_critic_disabled_returns_translate():
    builder = SegmentGraphBuilder(_FakeCtx(critic_enabled=False))
    state = {"aborted": False, "skip": False}
    assert builder.route_after_write(state) == "translate_node"


def test_state_script_propagates_across_write_to_critic():
    """Regression: AGENTS.md 'atomic state' rule - script must survive the
    round-trip from write_script_node to critic_node."""
    builder = SegmentGraphBuilder(_FakeCtx())
    state = {"i": 1, "plan": {}, "context": ""}
    write_out = builder.write_script_node(state)
    assert "script" in write_out
    new_state = {**state, **write_out}
    assert new_state["script"] == "draft"
    critic_out = builder.critic_node(new_state)
    assert "critic_approved" in critic_out


def test_nodes_skip_or_abort():
    builder = SegmentGraphBuilder(_FakeCtx())
    for s_key in ("aborted", "skip"):
        state = {s_key: True}
        assert builder.write_script_node(state) == {}
        assert builder.critic_node(state) == {}
        assert builder.translate_node(state) == {}
        assert builder.tts_node(state) == {}
        assert builder.image_node(state) == {}
        assert builder.render_node(state) == {}
