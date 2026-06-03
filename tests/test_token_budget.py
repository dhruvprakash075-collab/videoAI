"""test_token_budget.py - Tests for B4: CLIP 77-token budget wiring."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.scene_director import _cap_tokens, assemble_prompt, assemble_prompt_multi


def _count_tokens(text: str) -> int:
    """Approximate CLIP token count (word * 1.3)."""
    return max(1, int(len(text.split()) * 1.3)) if text.strip() else 0


def test_assemble_prompt_under_budget_unchanged():
    """Short prompts under budget should pass through intact."""
    identity = "young hero, brown eyes"
    scene = "standing at cliff edge"
    style = "anime style"
    result = assemble_prompt(identity, scene, style, budget=70)
    assert "young hero" in result
    assert "standing at cliff edge" in result


def test_assemble_prompt_over_budget_drops_style_tail():
    """When over budget, style tokens should be trimmed first, identity preserved."""
    identity = "young adult, warm brown eyes, short black hair, determined expression, dark grey coat, athletic build"
    scene = "standing at the edge of a cliff, stormy sky, dramatic lighting"
    style = "anime style, webtoon art, soft cell shading, high quality, 8k, masterpiece, detailed, cinematic"
    result = assemble_prompt(identity, scene, style, budget=40)
    # Identity should survive (placed first)
    assert "young adult" in result or "brown eyes" in result
    # Total tokens should be within budget
    assert _count_tokens(result) <= 45  # small tolerance


def test_assemble_prompt_identity_placed_first():
    """Identity tokens must appear before scene tokens in the output."""
    identity = "UNIQUE_IDENTITY_TOKEN"
    scene = "UNIQUE_SCENE_TOKEN"
    style = "UNIQUE_STYLE_TOKEN"
    result = assemble_prompt(identity, scene, style, budget=70)
    id_pos = result.find("UNIQUE_IDENTITY_TOKEN")
    scene_pos = result.find("UNIQUE_SCENE_TOKEN")
    assert id_pos < scene_pos, "Identity must come before scene"


def test_assemble_prompt_multi_proportional_budget():
    """Multi-character assembler should allocate budget proportionally."""
    identities = [
        ("hero, brown eyes, dark coat", 0.8),
        ("mentor, grey beard, wise expression", 0.4),
    ]
    scene = "two figures standing in fog"
    style = "anime style"
    result = assemble_prompt_multi(identities, scene, style, budget=50)
    # Both characters should appear (at least partially)
    assert "hero" in result or "brown eyes" in result
    assert _count_tokens(result) <= 55  # small tolerance


def test_cap_tokens_respects_limit():
    """_cap_tokens should not exceed the specified token limit (word count)."""
    long_text = ", ".join([f"token{i}" for i in range(100)])
    capped = _cap_tokens(long_text, max_tokens=20)
    # _cap_tokens caps by word*1.3 estimate; the actual word count should be ≤ max_tokens
    word_count = len(capped.split())
    assert word_count <= 20, f"Expected ≤20 words, got {word_count}"


def test_enrich_prompts_uses_config_budget():
    """enrich_prompts should pass config token_budget to assemble_prompt."""
    from utils.scene_director import enrich_prompts

    config = {
        "visual": {"style": "anime style"},
        "characters": {
            "hero": {"name": "The Hero", "description": "young adult, brown eyes, dark coat"}
        },
        "image_gen": {"token_budget": {"identity": 20, "style": 15, "scene": 25}},
    }
    plan = {"char_presence": [{"hero": 0.9}]}
    result_str, _neg = enrich_prompts(
        "hero stands on cliff", "The hero stood bravely.", config, plan
    )
    # Should not crash and should produce a non-empty result
    assert result_str.strip()
    assert _count_tokens(result_str.split(";")[0]) <= 65
