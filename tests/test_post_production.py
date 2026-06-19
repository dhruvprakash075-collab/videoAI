"""test_post_production.py - Tests for core/post_production.py (finalization, manifest, chapters).

Covers write_manifest, _write_chapters, _write_dry_run_chapters,
_generate_thumbnail, finalize_dry_run, and finalize_production.
"""

import json
import sys
import types
from unittest.mock import MagicMock, patch

import core.post_production as pp
import utils

# ── helpers ───────────────────────────────────────────────────────────────────


def _minimal_config(**overrides):
    cfg = {
        "video": {"output_path": "", "fps": 24, "resolution": "1920x1080"},
        "image_gen": {"sd_model_path": "sd.safetensors", "steps": 20, "width": 512, "height": 512},
        "tts": {"model": "omnivoice", "lang": "hi"},
        "models": {"director": "hermes", "writer": "zephyr"},
        "music": {"enabled": False},
        "upload": {"enabled": False},
    }
    cfg.update(overrides)
    return cfg


def _outline(n=2):
    return [
        {"title": f"Part {i}", "key_event": f"event {i}", "mood": "calm"} for i in range(1, n + 1)
    ]


def _stub_youtube_uploader(upload_mock):
    module = types.ModuleType("utils.youtube_uploader")
    module.upload_to_youtube = upload_mock
    sys.modules["utils.youtube_uploader"] = module
    utils.youtube_uploader = module


# ── write_manifest ────────────────────────────────────────────────────────────


class TestWriteManifest:
    def test_creates_manifest_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pp.write_manifest("TestTopic", {"status": "success"}, _minimal_config(), 3, 120.5)
        manifest_path = tmp_path / "studio_outputs" / "TestTopic" / "run_manifest.json"
        assert manifest_path.exists()
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert data["topic"] == "TestTopic"
        assert data["wall_time_seconds"] == 120.5
        assert data["status"] == "success"

    def test_includes_model_info(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _minimal_config()
        pp.write_manifest("T", {"status": "dry_run"}, cfg, 2, 10.0)
        manifest_path = tmp_path / "studio_outputs" / "T" / "run_manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert data["models"]["director"] == "hermes"
        assert data["models"]["writer"] == "zephyr"

    def test_includes_settings(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pp.write_manifest("T", {}, _minimal_config(), 1, 5.0)
        manifest_path = tmp_path / "studio_outputs" / "T" / "run_manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert data["settings"]["fps"] == 24

    def test_degradations_from_uistate(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        fake_uis = MagicMock()
        fake_uis.degradations = ["tts_fallback"]
        fake_uis.warning_count = 1
        fake_uis.vram_peaks = []
        fake_uis.segment_manifests = {"seg1": {"id": 1}}
        fake_uis.list_segment_manifests.return_value = [{"id": 1}]
        fake_uis.run_id = "test-run-id"
        with patch.dict("sys.modules", {"agents.ui_state": MagicMock(UIState=fake_uis)}):
            pp.write_manifest("T", {}, _minimal_config(), 1, 1.0)
        manifest_path = tmp_path / "studio_outputs" / "T" / "run_manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert "tts_fallback" in data["degradations"]

    def test_degradations_fallback_on_import_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("core.post_production._json.dumps", wraps=json.dumps):
            with patch.dict("sys.modules", {"agents.director_agent": None}):
                pp.write_manifest("T", {}, _minimal_config(), 1, 1.0)
        # No crash, degradations defaults to []
        manifest_path = tmp_path / "studio_outputs" / "T" / "run_manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert data["degradations"] == []

    def test_includes_thumbnail_if_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Pre-create the thumbnail
        out_dir = tmp_path / "studio_outputs" / "T"
        out_dir.mkdir(parents=True)
        thumb = out_dir / "thumbnail.png"
        thumb.write_bytes(b"PNG")
        pp.write_manifest("T", {}, _minimal_config(), 1, 1.0)
        data = json.loads((out_dir / "run_manifest.json").read_text())
        assert "thumbnail" in data

    def test_includes_decisions_from_blackboard(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        fake_rec = MagicMock()
        fake_rec.provenance_report.return_value = {"key": "val"}
        fake_bb = MagicMock()
        fake_bb.read_decision.return_value = fake_rec
        fake_bb_mod = MagicMock()
        fake_bb_mod.get_blackboard.return_value = fake_bb
        with patch.dict("sys.modules", {"memory.blackboard": fake_bb_mod}):
            pp.write_manifest("T", {}, _minimal_config(), 1, 1.0)
        manifest_path = tmp_path / "studio_outputs" / "T" / "run_manifest.json"
        data = json.loads(manifest_path.read_text())
        assert data.get("decisions") == {"key": "val"}

    def test_blackboard_failure_is_silent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        fake_bb_mod = MagicMock()
        fake_bb_mod.get_blackboard.side_effect = RuntimeError("bb error")
        with patch.dict("sys.modules", {"memory.blackboard": fake_bb_mod}):
            pp.write_manifest("T", {}, _minimal_config(), 1, 1.0)
        manifest_path = tmp_path / "studio_outputs" / "T" / "run_manifest.json"
        assert manifest_path.exists()


# ── _write_chapters ────────────────────────────────────────────────────────────


class TestWriteChapters:
    def test_writes_chapter_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        final_out = tmp_path / "studio_outputs" / "test" / "final.mp4"
        final_out.parent.mkdir(parents=True)
        outline = _outline(3)
        mp4s = [MagicMock(), MagicMock(), MagicMock()]

        with patch("core.post_production.get_video_duration", return_value=60.0):
            result = pp._write_chapters(outline, mp4s, final_out, "test")

        chapters_path = tmp_path / "studio_outputs" / "test" / "chapters.txt"
        assert chapters_path.exists()
        assert len(result) == 3

    def test_chapter_times_accumulate(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        final_out = tmp_path / "studio_outputs" / "t2" / "final.mp4"
        final_out.parent.mkdir(parents=True)
        outline = [{"title": "A"}, {"title": "B"}]
        mp4s = [MagicMock(), MagicMock()]

        with patch("core.post_production.get_video_duration", return_value=30.0):
            result = pp._write_chapters(outline, mp4s, final_out, "t2")

        # format_chapters_time uses H:MM:SS format — first chapter always starts at 0
        assert "0:00" in result[0]
        assert "0:30" in result[1] or "30" in result[1]

    def test_chapter_duration_matches_video_path_after_skipped_segment(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        final_out = tmp_path / "studio_outputs" / "skip" / "final.mp4"
        final_out.parent.mkdir(parents=True)
        seg1 = tmp_path / "segment_1.mp4"
        seg3 = tmp_path / "segment_3.mp4"
        manifests = [
            {"segment": 1, "video_path": str(seg1), "duration_seconds": 10.0},
            {"segment": 3, "video_path": str(seg3), "duration_seconds": 30.0},
        ]

        with patch("agents.ui_state.UIState.list_segment_manifests", return_value=manifests):
            result = pp._write_chapters(_outline(2), [seg1, seg3], final_out, "skip")

        assert "0:10" in result[1]

    def test_uses_key_event_if_no_title(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        final_out = tmp_path / "studio_outputs" / "t3" / "final.mp4"
        final_out.parent.mkdir(parents=True)
        outline = [{"key_event": "big battle"}]
        mp4s = [MagicMock()]

        with patch("core.post_production.get_video_duration", return_value=0.0):
            result = pp._write_chapters(outline, mp4s, final_out, "t3")

        assert "big battle" in result[0]

    def test_handles_fewer_mp4s_than_segments(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        final_out = tmp_path / "studio_outputs" / "t4" / "final.mp4"
        final_out.parent.mkdir(parents=True)
        outline = _outline(3)
        mp4s = [MagicMock()]  # Only 1 mp4 for 3 segments

        with patch("core.post_production.get_video_duration", return_value=20.0):
            result = pp._write_chapters(outline, mp4s, final_out, "t4")

        assert len(result) == 3  # Still generates all chapters

    def test_also_writes_chapters_next_to_final_video(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        final_out = tmp_path / "studio_outputs" / "t5" / "myfinal.mp4"
        final_out.parent.mkdir(parents=True)
        outline = _outline(1)
        mp4s = [None]  # None mp4 = no duration

        with patch("core.post_production.get_video_duration", return_value=0.0):
            pp._write_chapters(outline, mp4s, final_out, "t5")

        side_chapters = final_out.parent / "myfinal_chapters.txt"
        assert side_chapters.exists()

    def test_exception_returns_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        final_out = tmp_path / "final.mp4"
        outline = _outline(1)
        mp4s = [MagicMock()]

        with patch("core.post_production.format_chapters_time", side_effect=RuntimeError("boom")):
            result = pp._write_chapters(outline, mp4s, final_out, "t6")

        assert result == []


# ── _write_dry_run_chapters ────────────────────────────────────────────────────


class TestWriteDryRunChapters:
    def test_writes_chapters_with_30s_intervals(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        final_out = tmp_path / "studio_outputs" / "dry" / "final.mp4"
        final_out.parent.mkdir(parents=True)
        outline = _outline(3)

        result = pp._write_dry_run_chapters(outline, final_out, "dry")

        assert len(result) == 3
        assert "0:00" in result[0]
        # 30s interval — verify time increases
        assert result[1] != result[0]
        assert result[2] != result[1]

    def test_creates_chapters_txt(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        final_out = tmp_path / "studio_outputs" / "dry2" / "final.mp4"
        final_out.parent.mkdir(parents=True)
        outline = _outline(2)
        pp._write_dry_run_chapters(outline, final_out, "dry2")

        chapters_path = tmp_path / "studio_outputs" / "dry2" / "chapters.txt"
        assert chapters_path.exists()

    def test_also_writes_side_chapters(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        final_out = tmp_path / "studio_outputs" / "dry3" / "output.mp4"
        final_out.parent.mkdir(parents=True)
        outline = _outline(1)
        pp._write_dry_run_chapters(outline, final_out, "dry3")

        side = final_out.parent / "output_chapters.txt"
        assert side.exists()

    def test_exception_returns_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        final_out = tmp_path / "final.mp4"
        outline = _outline(1)

        with patch("core.post_production.format_chapters_time", side_effect=RuntimeError("boom")):
            result = pp._write_dry_run_chapters(outline, final_out, "dry_fail")

        assert result == []


# ── _generate_thumbnail ────────────────────────────────────────────────────────


class TestGenerateThumbnail:
    def test_returns_none_if_video_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = pp._generate_thumbnail(tmp_path / "nonexistent.mp4", "topic")
        assert result is None

    def test_returns_path_when_ffmpeg_creates_thumb(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        video = tmp_path / "video.mp4"
        video.write_bytes(b"x")
        # _generate_thumbnail creates the thumb at studio_outputs/<safe_topic>/thumbnail.png
        # _safe_filename("topic") == "topic" so:
        thumb_dir = tmp_path / "studio_outputs" / "topic"

        def fake_run(cmd, **kwargs):
            # Create the thumbnail to simulate ffmpeg success
            thumb_dir.mkdir(parents=True, exist_ok=True)
            (thumb_dir / "thumbnail.png").write_bytes(b"PNG")
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run):
            result = pp._generate_thumbnail(video, "topic")

        assert result is not None
        assert "thumbnail.png" in result

    def test_returns_none_when_ffmpeg_produces_no_output(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        video = tmp_path / "video.mp4"
        video.write_bytes(b"x")

        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            result = pp._generate_thumbnail(video, "topicX")

        assert result is None

    def test_returns_none_on_exception(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        video = tmp_path / "video.mp4"
        video.write_bytes(b"x")

        with patch("subprocess.run", side_effect=RuntimeError("ffmpeg not found")):
            result = pp._generate_thumbnail(video, "topicY")

        assert result is None


# ── finalize_dry_run ──────────────────────────────────────────────────────────


class TestFinalizeDryRun:
    def test_returns_dry_run_status(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _minimal_config()
        result = pp.finalize_dry_run("Topic", cfg, _outline(2), 2, [], 10.0)
        assert result["status"] == "dry_run"
        assert result["segments"] == 0

    def test_uses_config_output_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        custom_out = tmp_path / "custom_output.mp4"
        cfg = _minimal_config(video={"output_path": str(custom_out), "fps": 24})
        result = pp.finalize_dry_run("T", cfg, _outline(1), 1, [], 5.0)
        assert result["output"] == str(custom_out)

    def test_uses_default_output_path_when_config_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _minimal_config()  # video.output_path = ""
        result = pp.finalize_dry_run("MyTopic", cfg, _outline(1), 1, [], 5.0)
        assert "MyTopic" in result["output"] or "my_topic" in result["output"].lower()

    def test_writes_manifest(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _minimal_config()
        pp.finalize_dry_run("T", cfg, _outline(1), 1, [], 1.0)
        manifest = tmp_path / "studio_outputs" / "T" / "run_manifest.json"
        assert manifest.exists()

    def test_generates_chapters(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _minimal_config()
        result = pp.finalize_dry_run("T", cfg, _outline(2), 2, [], 1.0)
        assert "chapters" in result
        assert len(result["chapters"]) == 2

    def test_returns_segments_count(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _minimal_config()
        mp4s = [MagicMock(), MagicMock(), MagicMock()]
        result = pp.finalize_dry_run("T", cfg, _outline(3), 3, mp4s, 1.0)
        assert result["segments"] == 3


# ── finalize_production ───────────────────────────────────────────────────────


class TestFinalizeProduction:
    def _run_production(self, tmp_path, monkeypatch, mp4s=None, cfg_overrides=None, qc_result=None):
        monkeypatch.chdir(tmp_path)
        cfg = _minimal_config(**(cfg_overrides or {}))
        mp4s = mp4s or [tmp_path / "seg1.mp4"]

        fake_final = tmp_path / "final.mp4"
        fake_final.write_bytes(b"x")

        qc = qc_result or {"passed": True, "issues": [], "details": {"duration_s": 60.0}}

        with (
            patch("core.segment_runner.log_vram_usage"),  # imported inside finalize_production
            patch("utils.quality_check.check_video", return_value=qc),
            patch("video.renderer.assembler.concatenate_segments", return_value=fake_final),
            patch("core.post_production.get_video_duration", return_value=30.0),
        ):
            return pp.finalize_production("Topic", cfg, _outline(1), 1, mp4s, 5.0)

    def test_success_result(self, tmp_path, monkeypatch):
        result = self._run_production(tmp_path, monkeypatch)
        assert result["status"] == "success"

    def test_output_path_in_result(self, tmp_path, monkeypatch):
        result = self._run_production(tmp_path, monkeypatch)
        assert "output" in result

    def test_quality_check_included(self, tmp_path, monkeypatch):
        result = self._run_production(tmp_path, monkeypatch)
        assert result["quality"]["passed"] is True

    def test_qc_failure_returns_error_status(self, tmp_path, monkeypatch):
        qc = {"passed": False, "issues": ["short video"], "details": {"duration_s": 5.0}}
        result = self._run_production(tmp_path, monkeypatch, qc_result=qc)
        assert result["status"] == "error"
        assert result["quality"]["passed"] is False
        assert "output" in result  # file still available for inspection

    def test_concat_exception_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _minimal_config()

        with (
            patch("core.segment_runner.log_vram_usage"),
            patch(
                "video.renderer.assembler.concatenate_segments",
                side_effect=RuntimeError("concat fail"),
            ),
            patch(
                "utils.quality_check.check_video",
                return_value={"passed": True, "issues": [], "details": {}},
            ),
        ):
            result = pp.finalize_production("T", cfg, _outline(1), 1, [], 5.0)

        assert result["status"] == "error"
        assert "concat fail" in result["reason"]

    def test_thumbnail_generated_when_enabled(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _minimal_config(video={"output_path": "", "fps": 24, "generate_thumbnail": True})

        fake_final = tmp_path / "final.mp4"
        fake_final.write_bytes(b"x")

        with (
            patch("core.segment_runner.log_vram_usage"),
            patch(
                "utils.quality_check.check_video",
                return_value={"passed": True, "issues": [], "details": {"duration_s": 60.0}},
            ),
            patch("video.renderer.assembler.concatenate_segments", return_value=fake_final),
            patch("core.post_production.get_video_duration", return_value=30.0),
            patch(
                "core.post_production._generate_thumbnail", return_value="/path/to/thumb.png"
            ) as gen_thumb,
        ):
            result = pp.finalize_production("T", cfg, _outline(1), 1, [], 5.0)

        gen_thumb.assert_called_once()
        assert result["thumbnail"] == "/path/to/thumb.png"

    def test_thumbnail_skipped_when_disabled(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _minimal_config(video={"output_path": "", "fps": 24, "generate_thumbnail": False})

        fake_final = tmp_path / "final.mp4"
        fake_final.write_bytes(b"x")

        with (
            patch("core.segment_runner.log_vram_usage"),
            patch(
                "utils.quality_check.check_video",
                return_value={"passed": True, "issues": [], "details": {"duration_s": 60.0}},
            ),
            patch("video.renderer.assembler.concatenate_segments", return_value=fake_final),
            patch("core.post_production.get_video_duration", return_value=30.0),
            patch("core.post_production._generate_thumbnail") as gen_thumb,
        ):
            pp.finalize_production("T", cfg, _outline(1), 1, [], 5.0)

        gen_thumb.assert_not_called()

    def test_music_track_picked_by_mood(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        track = tmp_path / "calm.mp3"
        track.write_bytes(b"MP3")
        cfg = _minimal_config(
            music={
                "enabled": True,
                "mood_tracks": {"calm": str(track)},
            }
        )

        fake_final = tmp_path / "final.mp4"
        fake_final.write_bytes(b"x")

        outline = [{"title": "A", "mood": "calm"}]

        with (
            patch("core.segment_runner.log_vram_usage"),
            patch(
                "utils.quality_check.check_video",
                return_value={"passed": True, "issues": [], "details": {"duration_s": 60.0}},
            ),
            patch(
                "video.renderer.assembler.concatenate_segments", return_value=fake_final
            ) as concat_mock,
            patch("core.post_production.get_video_duration", return_value=30.0),
        ):
            pp.finalize_production("T", cfg, outline, 1, [], 5.0)

        # concatenate_segments called with music keyword
        call_args = concat_mock.call_args
        music_arg = call_args[1].get("music") if call_args[1] else None
        if music_arg is None and len(call_args[0]) > 2:
            music_arg = call_args[0][2]
        assert music_arg == track

    def test_music_track_missing_sets_none(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _minimal_config(
            music={
                "enabled": True,
                "mood_tracks": {"calm": "/nonexistent/track.mp3"},
            }
        )

        fake_final = tmp_path / "final.mp4"
        fake_final.write_bytes(b"x")

        outline = [{"title": "A", "mood": "calm"}]

        with (
            patch("core.segment_runner.log_vram_usage"),
            patch(
                "utils.quality_check.check_video",
                return_value={"passed": True, "issues": [], "details": {"duration_s": 60.0}},
            ),
            patch(
                "video.renderer.assembler.concatenate_segments", return_value=fake_final
            ) as concat_mock,
            patch("core.post_production.get_video_duration", return_value=30.0),
        ):
            pp.finalize_production("T", cfg, outline, 1, [], 5.0)

        # music=None should be passed when track doesn't exist
        call_args = concat_mock.call_args
        music_arg = call_args[1].get("music") if call_args[1] else None
        if music_arg is None and len(call_args[0]) > 2:
            music_arg = call_args[0][2]
        assert music_arg is None

    def test_writes_manifest_on_success(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._run_production(tmp_path, monkeypatch)
        # Find the manifest (safe_filename may transform "Topic")
        out_dirs = list((tmp_path / "studio_outputs").glob("*"))
        assert len(out_dirs) >= 1
        manifests = list((tmp_path / "studio_outputs").rglob("run_manifest.json"))
        assert len(manifests) >= 1
        data = json.loads(manifests[0].read_text())
        assert data["status"] == "success"

    def test_chapters_included_in_result(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _minimal_config()
        mp4s = [tmp_path / "seg1.mp4"]
        fake_final = tmp_path / "final.mp4"
        fake_final.write_bytes(b"x")

        with (
            patch("core.segment_runner.log_vram_usage"),
            patch(
                "utils.quality_check.check_video",
                return_value={"passed": True, "issues": [], "details": {"duration_s": 60.0}},
            ),
            patch("video.renderer.assembler.concatenate_segments", return_value=fake_final),
            patch("core.post_production.get_video_duration", return_value=30.0),
            patch("core.post_production._write_chapters", return_value=["0:00 Part 1"]),
        ):
            result = pp.finalize_production("T", cfg, _outline(1), 1, mp4s, 5.0)

        assert "chapters" in result

    def test_youtube_upload_triggered_when_enabled(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _minimal_config(
            upload={"enabled": True, "platform": "youtube", "visibility": "private"}
        )
        mp4s = [tmp_path / "seg1.mp4"]
        fake_final = tmp_path / "final.mp4"
        fake_final.write_bytes(b"x")

        fake_seo = {"title": "Test Title", "tags": ["tag1"]}
        upload_mock = MagicMock(return_value=True)
        _stub_youtube_uploader(upload_mock)

        with (
            patch("core.segment_runner.log_vram_usage"),
            patch(
                "utils.quality_check.check_video",
                return_value={"passed": True, "issues": [], "details": {"duration_s": 60.0}},
            ),
            patch("video.renderer.assembler.concatenate_segments", return_value=fake_final),
            patch("core.post_production.get_video_duration", return_value=30.0),
            patch("core.post_production._write_chapters", return_value=["0:00 Part 1"]),
            patch("utils.seo_generator.generate_seo_metadata", return_value=fake_seo),
        ):
            result = pp.finalize_production("T", cfg, _outline(1), 1, mp4s, 5.0)

        upload_mock.assert_called_once()
        assert result["youtube_upload"] == "success"

    def test_youtube_upload_failure_records_failed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _minimal_config(upload={"enabled": True, "platform": "youtube"})
        mp4s = []
        fake_final = tmp_path / "final.mp4"
        fake_final.write_bytes(b"x")
        _stub_youtube_uploader(MagicMock(return_value=False))

        with (
            patch("core.segment_runner.log_vram_usage"),
            patch(
                "utils.quality_check.check_video",
                return_value={"passed": True, "issues": [], "details": {"duration_s": 60.0}},
            ),
            patch("video.renderer.assembler.concatenate_segments", return_value=fake_final),
            patch("core.post_production.get_video_duration", return_value=30.0),
            patch("core.post_production._write_chapters", return_value=[]),
            patch(
                "utils.seo_generator.generate_seo_metadata", return_value={"title": "T", "tags": []}
            ),
        ):
            result = pp.finalize_production("T", cfg, _outline(1), 1, mp4s, 5.0)

        assert result["youtube_upload"] == "failed"

    def test_filters_none_mp4s_from_concat(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _minimal_config()
        mp4s = [None, tmp_path / "seg2.mp4", None]
        fake_final = tmp_path / "final.mp4"
        fake_final.write_bytes(b"x")

        with (
            patch("core.segment_runner.log_vram_usage"),
            patch(
                "utils.quality_check.check_video",
                return_value={"passed": True, "issues": [], "details": {"duration_s": 60.0}},
            ),
            patch(
                "video.renderer.assembler.concatenate_segments", return_value=fake_final
            ) as concat_mock,
            patch("core.post_production.get_video_duration", return_value=30.0),
        ):
            pp.finalize_production("T", cfg, _outline(1), 1, mp4s, 5.0)

        # First positional arg should be the list of non-None mp4s
        passed_mp4s = concat_mock.call_args[0][0]
        assert None not in passed_mp4s
