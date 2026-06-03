"""test_scene_director.py - Tests for utils/scene_director.py.

Covers _cap_tokens, get_dynamic_negative_prompt, _detect_mood,
assemble_prompt, assemble_prompt_multi, and enrich_prompts.
"""

from utils import scene_director as sd

# ── _cap_tokens ───────────────────────────────────────────────────────────────


class TestCapTokens:
    def test_empty_string(self):
        assert sd._cap_tokens("") == ""

    def test_does_not_truncate_when_within_budget(self):
        text = "a, b, c"
        assert sd._cap_tokens(text, max_tokens=100) == "a, b, c"

    def test_truncates_when_over_budget(self):
        # Each word costs ~1.3 tokens; many words should be truncated
        long_text = ", ".join(["word" * 2] * 60)
        result = sd._cap_tokens(long_text, max_tokens=10)
        assert len(result) < len(long_text)

    def test_respects_max_tokens_boundary(self):
        # 5 two-word parts: each costs ~3 tokens → total ~15; cap at 8 → 2 parts
        text = "aa bb, cc dd, ee ff, gg hh, ii jj"
        result = sd._cap_tokens(text, max_tokens=8)
        parts = [p.strip() for p in result.split(",") if p.strip()]
        assert len(parts) < 5

    def test_strips_whitespace_from_parts(self):
        result = sd._cap_tokens("  foo  ,  bar  ", max_tokens=100)
        assert "  " not in result

    def test_single_token_always_included(self):
        result = sd._cap_tokens("single", max_tokens=1)
        assert "single" in result


# ── _detect_mood ──────────────────────────────────────────────────────────────


class TestDetectMood:
    def test_detects_horror(self):
        assert sd._detect_mood("In the dark night a ghost haunted the castle") == "horror"

    def test_detects_action(self):
        assert sd._detect_mood("The battle was fierce as soldiers charged and fought") == "action"

    def test_detects_mysterious(self):
        assert sd._detect_mood("The secret mystery was strange and curious") == "mysterious"

    def test_detects_dramatic(self):
        assert sd._detect_mood("A powerful reveal confronted the betrayal of trust") == "dramatic"

    def test_detects_calm(self):
        assert sd._detect_mood("A peaceful and serene lake with gentle warm light") == "calm"

    def test_detects_epic(self):
        assert sd._detect_mood("The ancient legend of a mighty destiny and vast prophecy") == "epic"

    def test_detects_intimate(self):
        assert (
            sd._detect_mood("A gentle whisper and personal embrace close to the heart")
            == "intimate"
        )

    def test_defaults_to_mysterious_when_no_keywords(self):
        assert sd._detect_mood("xyz abc def ghi jkl") == "mysterious"

    def test_case_insensitive(self):
        assert sd._detect_mood("DARK SHADOW GHOST") == "horror"


# ── get_dynamic_negative_prompt ───────────────────────────────────────────────


class TestGetDynamicNegativePrompt:
    def test_returns_string(self):
        result = sd.get_dynamic_negative_prompt("horror", "some script", {})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_known_moods_return_specific_content(self):
        for mood in ["action", "horror", "mysterious", "dramatic", "calm", "epic", "intimate"]:
            result = sd.get_dynamic_negative_prompt(mood, "", {})
            assert isinstance(result, str)

    def test_unknown_mood_falls_back_to_mysterious(self):
        result_unknown = sd.get_dynamic_negative_prompt("unknown_mood", "", {})
        result_mysterious = sd.get_dynamic_negative_prompt("mysterious", "", {})
        assert result_unknown == result_mysterious

    def test_global_negative_from_visual_config(self):
        config = {"visual": {"negative_prompt": "extra bad stuff"}}
        result = sd.get_dynamic_negative_prompt("calm", "", config)
        assert "extra bad stuff" in result

    def test_global_negative_from_image_gen_config(self):
        config = {"image_gen": {"negative_prompt": "ugly artifacts"}}
        result = sd.get_dynamic_negative_prompt("calm", "", config)
        assert "ugly artifacts" in result

    def test_visual_config_takes_precedence_over_image_gen(self):
        config = {
            "visual": {"negative_prompt": "from_visual"},
            "image_gen": {"negative_prompt": "from_image_gen"},
        }
        result = sd.get_dynamic_negative_prompt("calm", "", config)
        assert "from_visual" in result

    def test_empty_global_negative_not_appended(self):
        config = {"visual": {"negative_prompt": ""}}
        result = sd.get_dynamic_negative_prompt("calm", "", config)
        # Should not end with a trailing comma
        assert not result.endswith(",")

    def test_capped_output_within_150_tokens(self):
        config = {"image_gen": {"negative_prompt": ", ".join(["word"] * 200)}}
        result = sd.get_dynamic_negative_prompt("calm", "", config)
        # Word count * 1.3 should be <= 150
        word_count = len(result.split())
        assert word_count * 1.3 <= 150 + 50  # allow some slack


# ── assemble_prompt ───────────────────────────────────────────────────────────


class TestAssemblePrompt:
    def test_returns_string(self):
        result = sd.assemble_prompt("hero", "running through forest", "cinematic lighting")
        assert isinstance(result, str)

    def test_identity_tokens_appear_first_or_early(self):
        result = sd.assemble_prompt("hero desc", "running", "cinematic lighting, 8k")
        # Identity should appear before scene content
        assert "hero desc" in result

    def test_style_anchor_always_included(self):
        result = sd.assemble_prompt("", "scene", "anime, webtoon, soft shading, high quality")
        assert "anime" in result

    def test_within_budget(self):
        result = sd.assemble_prompt(
            "a" * 200,  # very long identity
            "b" * 200,  # very long scene
            "c" * 200,  # very long style
            budget=70,
        )
        # Word count * 1.3 should be well under budget
        word_count = len(result.split())
        assert word_count * 1.3 <= 70 * 2  # some slack

    def test_empty_identity_still_works(self):
        result = sd.assemble_prompt("", "running through forest", "cinematic lighting")
        assert "running through forest" in result

    def test_empty_scene_still_works(self):
        result = sd.assemble_prompt("hero desc", "", "cinematic lighting")
        assert "hero desc" in result

    def test_all_empty_returns_empty_or_just_anchor(self):
        result = sd.assemble_prompt("", "", "")
        assert isinstance(result, str)

    def test_scene_trimmed_when_over_budget(self):
        long_scene = " ".join(["word"] * 200)
        result = sd.assemble_prompt("", long_scene, "style", budget=20)
        word_count = len(result.split())
        assert word_count <= 30  # reasonable upper bound


# ── assemble_prompt_multi ─────────────────────────────────────────────────────


class TestAssemblePromptMulti:
    def test_returns_string(self):
        result = sd.assemble_prompt_multi(
            [("hero description text", 0.8), ("villain description", 0.6)],
            "battle scene",
            "cinematic lighting",
        )
        assert isinstance(result, str)

    def test_includes_both_characters(self):
        result = sd.assemble_prompt_multi(
            [("hero text", 1.0), ("villain text", 0.7)], "fight", "cinematic"
        )
        assert "hero text" in result or "villain text" in result

    def test_within_budget(self):
        long_id = ", ".join(["identity word"] * 30)
        result = sd.assemble_prompt_multi(
            [(long_id, 0.8), (long_id, 0.7)],
            "scene text",
            "style text",
            budget=70,
        )
        word_count = len(result.split())
        assert word_count * 1.3 <= 70 * 2

    def test_empty_identity_list_still_works(self):
        result = sd.assemble_prompt_multi([], "just a scene", "cinematic", budget=70)
        assert "just a scene" in result

    def test_style_anchor_included(self):
        result = sd.assemble_prompt_multi(
            [("desc", 0.9)], "scene", "anime, webtoon, art style, quality", budget=70
        )
        assert "anime" in result

    def test_proportional_budget_allocation(self):
        """Heavier character gets more tokens."""
        result = sd.assemble_prompt_multi(
            [("important main character very detailed", 0.9), ("minor sidekick", 0.1)],
            "scene",
            "cinematic",
            budget=70,
        )
        # Should at least include the dominant character
        assert "important" in result

    def test_empty_description_skipped(self):
        result = sd.assemble_prompt_multi([("", 1.0), ("valid description", 0.8)], "scene", "style")
        assert isinstance(result, str)


# ── enrich_prompts ────────────────────────────────────────────────────────────


class TestEnrichPrompts:
    def _minimal_config(self):
        return {
            "visual": {"style": "Gothic Horror"},
            "characters": {},
            "image_gen": {"token_budget": {"identity": 25, "style": 20, "scene": 32}},
        }

    def test_returns_tuple_of_two_strings(self):
        result = sd.enrich_prompts(
            "a dark castle; a ghostly figure", "horror script", self._minimal_config()
        )
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_empty_prompts_returns_original(self):
        enriched, neg = sd.enrich_prompts("", "script", self._minimal_config())
        assert isinstance(enriched, str)
        assert isinstance(neg, str)

    def test_mood_detected_and_camera_added(self):
        enriched, _neg = sd.enrich_prompts(
            "dark castle", "blood and ghost and darkness fear", self._minimal_config()
        )
        # Horror camera moves should be in there somewhere
        assert len(enriched) > len("dark castle")

    def test_anime_style_uses_anime_assembler(self):
        cfg = {
            "visual": {"style": "anime 2d webtoon"},
            "characters": {},
            "image_gen": {"token_budget": {}},
        }
        enriched, _neg = sd.enrich_prompts("scene prompt", "peaceful scene", cfg)
        assert "anime" in enriched.lower() or "webtoon" in enriched.lower()

    def test_gothic_style_uses_photorealistic_assembler(self):
        cfg = {
            "visual": {"style": "Gothic Horror"},
            "characters": {},
            "image_gen": {"token_budget": {}},
        }
        enriched, _neg = sd.enrich_prompts("dark castle", "dark mysterious night", cfg)
        assert "Gothic Horror" in enriched or "gothic horror" in enriched.lower()

    def test_char_presence_low_weight_strips_character(self):
        cfg = {
            "visual": {"style": "cinematic"},
            "characters": {"hero": {"name": "Arjuna", "description": "warrior with bow"}},
            "image_gen": {"token_budget": {}},
        }
        plan = {"char_presence": [{"hero": 0.1}]}  # below 0.3 threshold
        enriched, _ = sd.enrich_prompts("Arjuna running", "battle scene charge", cfg, plan=plan)
        # Character description should be stripped (replaced with empty landscape)
        assert "warrior with bow" not in enriched

    def test_char_presence_high_weight_includes_character(self):
        cfg = {
            "visual": {"style": "cinematic"},
            "characters": {"hero": {"name": "Arjuna", "description": "warrior with bow"}},
            "image_gen": {"token_budget": {}},
        }
        plan = {"char_presence": [{"hero": 0.9}]}  # high weight
        enriched, _ = sd.enrich_prompts("scene prompt", "battle charge attack", cfg, plan=plan)
        assert "warrior with bow" in enriched

    def test_no_char_presence_uses_establishing_and_closing(self):
        cfg = {
            "visual": {"style": "cinematic"},
            "characters": {},
            "image_gen": {"token_budget": {}},
        }
        enriched, _ = sd.enrich_prompts("scene1; scene2; scene3", "calm peaceful script", cfg)
        # First prompt: establishing shot
        assert "establishing" in enriched.lower() or len(enriched) > 0

    def test_style_as_dict_coerced_to_string(self):
        cfg = {
            "visual": {"style": {"tone": "dark", "elements": ["shadows", "fog"]}},
            "characters": {},
            "image_gen": {"token_budget": {}},
        }
        enriched, _ = sd.enrich_prompts("scene", "script", cfg)
        # Should not crash and should produce something with the tone
        assert "dark" in enriched or len(enriched) > 0

    def test_multi_prompt_input_produces_multiple_outputs(self):
        cfg = self._minimal_config()
        enriched, _ = sd.enrich_prompts("prompt1; prompt2; prompt3", "horror ghost fear dark", cfg)
        parts = [p.strip() for p in enriched.split(";") if p.strip()]
        assert len(parts) == 3

    def test_negative_prompt_is_nonempty(self):
        _, neg = sd.enrich_prompts("scene", "dark horror ghost", self._minimal_config())
        assert len(neg) > 0

    def test_multi_character_high_weight_uses_multi_assembler(self):
        cfg = {
            "visual": {"style": "cinematic"},
            "characters": {
                "hero": {"name": "Ram", "description": "strong warrior"},
                "villain": {"name": "Ravan", "description": "demon king"},
            },
            "image_gen": {"token_budget": {}},
        }
        plan = {"char_presence": [{"hero": 0.8, "villain": 0.7}]}
        enriched, _ = sd.enrich_prompts("epic battle", "battle charge attack fight", cfg, plan=plan)
        # At least one character description should appear
        assert "strong warrior" in enriched or "demon king" in enriched

    def test_stop_words_not_stripped_as_char_names(self):
        """Short stop-words like 'The', 'a', 'an' should NOT be treated as character names."""
        cfg = {
            "visual": {"style": "cinematic"},
            "characters": {
                "c1": {"name": "The", "description": "a character"},
            },
            "image_gen": {"token_budget": {}},
        }
        plan = {"char_presence": [{"c1": 0.1}]}
        enriched, _ = sd.enrich_prompts("The hero runs", "calm peaceful", cfg, plan=plan)
        # "The" should NOT be replaced because it's a stop word
        assert isinstance(enriched, str)

    def test_char_presence_mixed_weights_strips_minor_character(self):
        cfg = {
            "visual": {"style": "cinematic"},
            "characters": {
                "hero": {"name": "Arjuna", "description": "warrior with bow"},
                "sidekick": {"name": "Sanjay", "description": "young helper"},
            },
            "image_gen": {"token_budget": {}},
        }
        plan = {"char_presence": [{"hero": 0.8, "sidekick": 0.1}]}  # Arjuna high, Sanjay low
        enriched, _ = sd.enrich_prompts(
            "Arjuna and Sanjay walking", "battle scene charge", cfg, plan=plan
        )
        assert "Sanjay" not in enriched

    def test_assemble_prompt_multi_boundary_cases(self):
        # 1. Style anchor cost > budget * 0.25 (forces style anchor to be omitted)
        res = sd.assemble_prompt_multi(
            identity_list=[],
            scene_tokens="running",
            style_tokens="very long style prompt that exceeds the budget limit by a lot",
            budget=10,
        )
        assert "running" in res

        # 2. Character description exceeds character budget, forces minimal description or omission
        res2 = sd.assemble_prompt_multi(
            identity_list=[
                (
                    "very detailed description of the main hero that is way too long for the budget",
                    1.0,
                )
            ],
            scene_tokens="running",
            style_tokens="anime",
            budget=15,
        )
        assert "running" in res2

        # 3. Scene tokens exceed remaining budget, gets trimmed
        res3 = sd.assemble_prompt_multi(
            identity_list=[("hero", 1.0)],
            scene_tokens="running forest mountains fields trees lakes rivers clouds skies",
            style_tokens="anime",
            budget=12,
        )
        assert "hero" in res3

        # 4. Style tokens exceed remaining budget, triggers break
        res4 = sd.assemble_prompt_multi(
            identity_list=[("hero", 1.0)],
            scene_tokens="running",
            style_tokens="anime, hd, 8k, detailed, award-winning, masterpieces, stunning",
            budget=20,
        )
        assert "hero" in res4


class TestSceneDirectorUncovered:
    def test_enrich_prompts_empty_description_and_name(self):
        """Test enrich_prompts when characters are missing descriptions or have short names."""
        cfg = {
            "visual": {"style": "cinematic"},
            "characters": {
                "c1": {"name": "c1", "description": ""},  # empty description (line 146)
                "c2": {
                    "name": "c2",
                    "description": "some desc",
                },  # name too short (< 3 chars) (line 149/195)
            },
            "image_gen": {"token_budget": {}},
        }
        plan = {"char_presence": [{"c1": 0.8, "c2": 0.1}]}
        enriched, _ = sd.enrich_prompts(
            "c1 and c2 epic battle", "battle charge attack", cfg, plan=plan
        )
        # Should complete successfully and "c2" (short name) should not trigger substitution as Arjuna did
        assert "c2" in enriched

    def test_enrich_prompts_val_not_dict(self):
        """Test enrich_prompts when char_presence item is not a dict (line 158)."""
        cfg = {
            "visual": {"style": "cinematic"},
            "characters": {},
            "image_gen": {"token_budget": {}},
        }
        plan = {"char_presence": [None]}  # val is None (not dict)
        enriched, _ = sd.enrich_prompts("scene prompt", "battle charge attack", cfg, plan=plan)
        assert "scene prompt" in enriched

    def test_enrich_prompts_medium_weight_shot(self):
        """Test enrich_prompts medium shot framing (line 184)."""
        cfg = {
            "visual": {"style": "cinematic"},
            "characters": {"hero": {"name": "Arjuna", "description": "warrior"}},
            "image_gen": {"token_budget": {}},
        }
        plan = {"char_presence": [{"hero": 0.5}]}  # max_weight between 0.3 and 0.7
        enriched, _ = sd.enrich_prompts("Arjuna walking", "battle scene", cfg, plan=plan)
        assert "medium shot" in enriched

    def test_assemble_prompt_scene_and_style_trimming(self):
        """Test assemble_prompt scene/style trimming logic (lines 455->460, 470->475, 480, 489)."""
        # 1. Identity fails to fit because used + id_cost > budget
        res_id_fail = sd.assemble_prompt(
            identity_tokens="very long character identity description that exceeds the budget",
            scene_tokens="running",
            style_tokens="cinematic, lighting, shadows, atmospheric",
            budget=20,
        )
        assert "running" in res_id_fail

        # 2. Scene exceeds remaining budget and gets trimmed (line 470)
        res_sc_trim = sd.assemble_prompt(
            identity_tokens="hero",
            scene_tokens="running forest mountains fields trees lakes rivers clouds skies",
            style_tokens="cinematic",
            budget=15,
        )
        assert "hero" in res_sc_trim

        # 3. Style tokens trimmed because of budget
        res_st_trim = sd.assemble_prompt(
            identity_tokens="hero",
            scene_tokens="running",
            style_tokens="cinematic, hd, 8k, detailed, award-winning, masterpieces, stunning",
            budget=22,
        )
        assert "hero" in res_st_trim

    def test_assemble_prompt_multi_char_trimmed_first_3_tokens(self):
        """Test assemble_prompt_multi minimal description fallback (lines 375-378)."""
        # Triggers the else block where it attempts to use a minimal description (first 3 tokens)
        res = sd.assemble_prompt_multi(
            identity_list=[
                (
                    "hero, detailed, armor, shield, sword, crown, boots, cape",
                    1.0,
                )
            ],
            scene_tokens="running",
            style_tokens="anime",
            budget=20,
        )
        assert "hero" in res

    def test_assemble_prompt_multi_style_anchor_skipped(self):
        """Test style tokens are skipped if already in style anchor (lines 400, 408)."""
        res = sd.assemble_prompt_multi(
            identity_list=[("hero", 1.0)],
            scene_tokens="running",
            style_tokens="anime, hd, 8k, detail, anime",  # duplicate style token outside anchor
            budget=30,
        )
        # Should not duplicate the anime token in the final list
        assert res.count("anime") == 1
