"""Tests for core/outline_shaping.py structural locks."""

from core.outline_shaping import shape_outline


def test_words_lock_seeds_target_word_count():
    outline = [
        {"seg": 1, "title": "Intro", "num_images": 2, "target_word_count": 250, "segment_duration": 60.0},
        {"seg": 2, "title": "Body", "num_images": 2, "target_word_count": 250, "segment_duration": 60.0},
    ]
    config = {"script": {"words_per_segment": 130}, "visual": {"environment_frame_ratio": 0.4}}
    shaped = shape_outline(outline, config, images_per_segment_locked=False, words_per_segment_locked=True)
    assert [seg["target_word_count"] for seg in shaped] == [130, 130]


def test_words_not_locked_keeps_director_target():
    outline = [
        {"seg": 1, "title": "Intro", "num_images": 2, "target_word_count": 250, "segment_duration": 60.0},
    ]
    config = {"script": {"words_per_segment": 130}, "visual": {"environment_frame_ratio": 0.4}}
    shaped = shape_outline(outline, config, images_per_segment_locked=False, words_per_segment_locked=False)
    assert shaped[0]["target_word_count"] == 250
