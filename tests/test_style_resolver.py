import yaml

from style_resolver import StyleResolver


def _styles_file(tmp_path):
    path = tmp_path / "styles.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "fallback_prompt": "fallback prompt",
                "styles": {
                    "anime": {
                        "prompt": "anime prompt",
                        "keywords": ["anime"],
                        "aliases": ["visual novel"],
                    },
                    "gothic": {
                        "prompt": "gothic prompt",
                        "keywords": ["gothic horror"],
                    },
                    "empty": {
                        "keywords": ["plain"],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_missing_styles_file_uses_builtin_fallback(tmp_path):
    resolver = StyleResolver(tmp_path / "missing.yaml")

    key, prompt = resolver.resolve("")

    assert key == "fallback"
    assert "anime visual novel" in prompt


def test_exact_match_strips_keep_as_is_prefix_and_uses_aliases(tmp_path):
    resolver = StyleResolver(_styles_file(tmp_path))

    assert resolver.resolve("keep as-is: visual novel")[0] == "anime"
    assert resolver.resolve("a gothic horror frame") == ("gothic", "gothic prompt")


def test_fuzzy_match_and_missing_prompt_falls_back(tmp_path):
    resolver = StyleResolver(_styles_file(tmp_path))

    assert resolver.resolve("gothic")[0] == "gothic"
    assert resolver.resolve("plain") == ("empty", "fallback prompt")


def test_llm_expansion_success_short_result_and_failure(tmp_path):
    resolver = StyleResolver(_styles_file(tmp_path))

    assert resolver.resolve("unknown", lambda _style: "this expanded prompt is definitely long enough") == (
        "llm_expanded",
        "this expanded prompt is definitely long enough",
    )
    assert resolver.resolve("unknown", lambda _style: "short") == ("fallback", "fallback prompt")

    def fail(_style):
        raise RuntimeError("down")

    assert resolver.resolve("unknown", fail) == ("fallback", "fallback prompt")


def test_reload_picks_up_file_changes(tmp_path):
    path = _styles_file(tmp_path)
    resolver = StyleResolver(path)

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["styles"]["anime"]["prompt"] = "new anime prompt"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    resolver.reload()

    assert resolver.resolve("anime") == ("anime", "new anime prompt")
