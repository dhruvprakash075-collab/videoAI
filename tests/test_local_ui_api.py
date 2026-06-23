"""Tests for the new local_ui.py endpoints: chat, preflight, artifacts, memory, characters, config/jobs extensions."""

from unittest.mock import MagicMock, mock_open, patch

from fastapi.testclient import TestClient

from utils.local_ui import app

client = TestClient(app)


# -------------------- Chat Tests --------------------

class TestChatEndpoint:
    @patch("utils.local_ui.get_ollama_client")
    @patch("utils.local_ui.load_config")
    def test_chat_successful_reply(self, mock_load_config, mock_get_ollama):
        mock_load_config.return_value = {"models": {"director": "llama3.1"}}
        mock_ollama = MagicMock()
        mock_ollama.chat.return_value = "Hello! I can help with that."
        mock_get_ollama.return_value = mock_ollama

        resp = client.post("/api/chat", json={"message": "hello", "session_id": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert data["reply"] == "Hello! I can help with that."
        assert data["session_id"]
        assert len(data["messages"]) == 2

    def test_chat_empty_message_rejected(self):
        resp = client.post("/api/chat", json={"message": "", "session_id": ""})
        assert resp.status_code == 400
        assert "message is required" in resp.json()["error"]

    @patch("utils.local_ui.get_ollama_client")
    @patch("utils.local_ui.load_config")
    def test_chat_model_failure_returns_error(self, mock_load_config, mock_get_ollama):
        mock_load_config.return_value = {"models": {"director": "llama3.1"}}
        mock_ollama = MagicMock()
        mock_ollama.chat.side_effect = RuntimeError("Ollama not reachable")
        mock_get_ollama.return_value = mock_ollama

        resp = client.post("/api/chat", json={"message": "test", "session_id": ""})
        assert resp.status_code == 500
        assert "Chat failed" in resp.json()["error"]

    @patch("utils.local_ui.get_ollama_client")
    @patch("utils.local_ui.load_config")
    def test_chat_session_persists_in_memory(self, mock_load_config, mock_get_ollama):
        mock_load_config.return_value = {"models": {"director": "llama3.1"}}
        mock_ollama = MagicMock()
        mock_ollama.chat.return_value = "ok"
        mock_get_ollama.return_value = mock_ollama

        resp1 = client.post("/api/chat", json={"message": "first", "session_id": ""})
        sid = resp1.json()["session_id"]

        resp2 = client.post("/api/chat", json={"message": "second", "session_id": sid})
        assert resp2.status_code == 200
        assert len(resp2.json()["messages"]) == 4  # 2 user + 2 assistant

    def test_delete_chat_session(self):
        with patch("utils.local_ui.get_ollama_client"), patch("utils.local_ui.load_config"):
            resp = client.post("/api/chat", json={"message": "test", "session_id": ""})
            sid = resp.json()["session_id"]

        resp = client.delete(f"/api/chat/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        resp = client.get(f"/api/chat/sessions/{sid}")
        assert resp.status_code == 404

    def test_delete_nonexistent_session(self):
        resp = client.delete("/api/chat/sessions/nonexistent")
        assert resp.status_code == 404


# -------------------- Preflight Tests --------------------

class TestPreflightEndpoint:
    @patch("utils.preflight.run_preflight")
    @patch("utils.local_ui.load_config")
    def test_preflight_returns_structured_checks(self, mock_load_config, mock_run_preflight):
        from utils.preflight import PreflightCheck
        mock_load_config.return_value = {}
        mock_result = MagicMock()
        mock_result.all_ok = True
        mock_result.checks = [
            PreflightCheck(name="python_version", status="ok", message="Python 3.12"),
            PreflightCheck(name="ollama", status="ok", message="Ollama reachable"),
            PreflightCheck(name="disk", status="warn", message="Only 3 GB free"),
        ]
        mock_run_preflight.return_value = mock_result

        resp = client.get("/api/preflight")
        assert resp.status_code == 200
        data = resp.json()
        assert data["all_ok"] is True
        assert len(data["checks"]) == 3
        assert data["checks"][0]["name"] == "python_version"
        assert data["checks"][0]["status"] == "ok"
        assert data["checks"][1]["name"] == "ollama"
        assert data["checks"][2]["status"] == "warn"

    @patch("utils.preflight.run_preflight")
    @patch("utils.local_ui.load_config")
    def test_preflight_error(self, mock_load_config, mock_run_preflight):
        mock_load_config.return_value = {}
        mock_run_preflight.side_effect = RuntimeError("Preflight crashed")
        resp = client.get("/api/preflight")
        assert resp.status_code == 500


# -------------------- Chat Tests --------------------

class TestChatEndpointExtended:
    @patch("utils.local_ui.get_ollama_client")
    @patch("utils.local_ui.load_config")
    def test_chat_successful_reply(self, mock_load_config, mock_get_ollama):
        mock_load_config.return_value = {"models": {"director": "llama3.1"}}
        mock_ollama = MagicMock()
        mock_ollama.chat.return_value = "Hello! I can help with that."
        mock_get_ollama.return_value = mock_ollama

        resp = client.post("/api/chat", json={"message": "hello", "session_id": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert data["reply"] == "Hello! I can help with that."
        assert data["session_id"]
        assert len(data["messages"]) == 2

    def test_chat_empty_message_rejected(self):
        resp = client.post("/api/chat", json={"message": "", "session_id": ""})
        assert resp.status_code == 400
        assert "message is required" in resp.json()["error"]

    @patch("utils.local_ui.get_ollama_client")
    @patch("utils.local_ui.load_config")
    def test_chat_model_failure_returns_error(self, mock_load_config, mock_get_ollama):
        mock_load_config.return_value = {"models": {"director": "llama3.1"}}
        mock_ollama = MagicMock()
        mock_ollama.chat.side_effect = RuntimeError("Ollama not reachable")
        mock_get_ollama.return_value = mock_ollama

        resp = client.post("/api/chat", json={"message": "test", "session_id": ""})
        assert resp.status_code == 500
        assert "Chat failed" in resp.json()["error"]

    @patch("utils.local_ui.get_ollama_client")
    @patch("utils.local_ui.load_config")
    def test_chat_session_persists_in_memory(self, mock_load_config, mock_get_ollama):
        mock_load_config.return_value = {"models": {"director": "llama3.1"}}
        mock_ollama = MagicMock()
        mock_ollama.chat.return_value = "ok"
        mock_get_ollama.return_value = mock_ollama

        resp1 = client.post("/api/chat", json={"message": "first", "session_id": ""})
        sid = resp1.json()["session_id"]

        resp2 = client.post("/api/chat", json={"message": "second", "session_id": sid})
        assert resp2.status_code == 200
        assert len(resp2.json()["messages"]) == 4  # 2 user + 2 assistant

    def test_delete_chat_session(self):
        with patch("utils.local_ui.get_ollama_client"), patch("utils.local_ui.load_config"):
            resp = client.post("/api/chat", json={"message": "test", "session_id": ""})
            sid = resp.json()["session_id"]

        resp = client.delete(f"/api/chat/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        resp = client.get(f"/api/chat/sessions/{sid}")
        assert resp.status_code == 404

    def test_delete_nonexistent_session(self):
        resp = client.delete("/api/chat/sessions/nonexistent")
        assert resp.status_code == 404


# -------------------- Preflight Tests --------------------

class TestPreflightEndpointExtended:
    @patch("utils.preflight.run_preflight")
    @patch("utils.local_ui.load_config")
    def test_preflight_returns_structured_checks(self, mock_load_config, mock_run_preflight):
        from utils.preflight import PreflightCheck
        mock_load_config.return_value = {}
        mock_result = MagicMock()
        mock_result.all_ok = True
        mock_result.checks = [
            PreflightCheck(name="python_version", status="ok", message="Python 3.12"),
            PreflightCheck(name="ollama", status="ok", message="Ollama reachable"),
            PreflightCheck(name="disk", status="warn", message="Only 3 GB free"),
        ]
        mock_run_preflight.return_value = mock_result

        resp = client.get("/api/preflight")
        assert resp.status_code == 200
        data = resp.json()
        assert data["all_ok"] is True
        assert len(data["checks"]) == 3
        assert data["checks"][0]["name"] == "python_version"
        assert data["checks"][0]["status"] == "ok"
        assert data["checks"][1]["name"] == "ollama"
        assert data["checks"][2]["status"] == "warn"

    @patch("utils.preflight.run_preflight")
    @patch("utils.local_ui.load_config")
    def test_preflight_error(self, mock_load_config, mock_run_preflight):
        mock_load_config.return_value = {}
        mock_run_preflight.side_effect = RuntimeError("Preflight crashed")
        resp = client.get("/api/preflight")
        assert resp.status_code == 500


# -------------------- Config Extension Tests --------------------

class TestConfigExtensionExtended:
    @patch("utils.local_ui.load_config")
    def test_post_config_validates_comfyui_paths(self, mock_load_config):
        mock_load_config.return_value = {"image_gen": {}, "tts": {}, "subtitles": {}, "script": {}}

        # Test root with .. traversal
        resp = client.post("/api/config", data={
            "voice_engine": "supertonic",
            "dynamic_subtitles": "false",
            "uncapped_scaling": "false",
            "max_images_per_segment": 6,
            "comfyui_root": "../invalid_dir",
        })
        assert resp.status_code == 400
        assert "comfyui_root must not contain '..' path parts" in resp.json()["message"]

        # Test root non-existent
        resp = client.post("/api/config", data={
            "voice_engine": "supertonic",
            "dynamic_subtitles": "false",
            "uncapped_scaling": "false",
            "max_images_per_segment": 6,
            "comfyui_root": "C:\\nonexistent_directory_abcdef",
        })
        assert resp.status_code == 400
        assert "comfyui_root path must exist and be a directory" in resp.json()["message"]

        # Test python file with .. traversal
        resp = client.post("/api/config", data={
            "voice_engine": "supertonic",
            "dynamic_subtitles": "false",
            "uncapped_scaling": "false",
            "max_images_per_segment": 6,
            "comfyui_python": "../invalid_py",
        })
        assert resp.status_code == 400
        assert "comfyui_python must not contain '..' path parts" in resp.json()["message"]

        # Test python file non-existent
        resp = client.post("/api/config", data={
            "voice_engine": "supertonic",
            "dynamic_subtitles": "false",
            "uncapped_scaling": "false",
            "max_images_per_segment": 6,
            "comfyui_python": "C:\\nonexistent_py_file_abcdef.exe",
        })
        assert resp.status_code == 400
        assert "comfyui_python path must exist and be a file" in resp.json()["message"]

        # Test workflow with .. traversal
        resp = client.post("/api/config", data={
            "voice_engine": "supertonic",
            "dynamic_subtitles": "false",
            "uncapped_scaling": "false",
            "max_images_per_segment": 6,
            "comfyui_workflow_path": "../invalid_wf.json",
        })
        assert resp.status_code == 400
        assert "comfyui_workflow_path must not contain '..' path parts" in resp.json()["message"]

        # Test workflow non-existent
        resp = client.post("/api/config", data={
            "voice_engine": "supertonic",
            "dynamic_subtitles": "false",
            "uncapped_scaling": "false",
            "max_images_per_segment": 6,
            "comfyui_workflow_path": "C:\\nonexistent_wf_abcdef.json",
        })
        assert resp.status_code == 400
        assert "comfyui_workflow_path must exist" in resp.json()["message"]

    def test_upload_script_size_limit(self):
        # Create a large script (11MB)
        large_content = b"a" * (11 * 1024 * 1024)
        resp = client.post(
            "/api/upload_script",
            data={"topic": "T"},
            files={"file": ("story.txt", large_content, "text/plain")},
        )
        assert resp.status_code == 413
        assert "File exceeds maximum allowed size" in resp.json()["message"]

    def test_upload_voice_size_limit(self):
        # Create a large voice (21MB)
        large_content = b"a" * (21 * 1024 * 1024)
        resp = client.post(
            "/api/upload_voice",
            data={"character_name": "testchar"},
            files={"file": ("voice.wav", large_content, "audio/wav")},
        )
        assert resp.status_code == 413
        assert "File exceeds maximum allowed size" in resp.json()["message"]

    def test_security_headers(self):
        resp = client.get("/")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("Referrer-Policy") == "no-referrer"


# -------------------- Jobs Extension Tests --------------------

class TestJobsExtension:
    @patch("utils.local_ui.job_store")
    def test_post_jobs_accepts_full_payload(self, mock_store):
        mock_store.create_job.return_value = 42
        mock_store.append_event = MagicMock()
        mock_store.list_jobs.return_value = []

        payload = {
            "topic": "Test Topic",
            "duration": 3,
            "dry_run": True,
            "no_resume": True,
            "skip_rvc": True,
            "project": "myproj",
            "series": True,
            "director_mode": False,
            "run_mode": "project",
            "eval_models": False,
            "preview": False,
            "skip_preflight": False,
            "preflight_only": False,
            "words_per_segment": 50,
            "images_per_segment": 4,
            "segment_count": 5,
            "yes": True,
            "source": "topic",
            "content_text": "story content",
        }
        resp = client.post("/api/jobs", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert data["job_id"] == 42
        assert data["request"]["topic"] == "Test Topic"
        assert data["request"]["dry_run"] is True

    def test_post_jobs_rejects_invalid_run_mode(self):
        resp = client.post("/api/jobs", json={"topic": "T", "run_mode": "../../bad"})

        assert resp.status_code == 400
        assert "run_mode must be one of" in resp.json()["message"]

    @patch("utils.local_ui.job_store")
    def test_upload_script_rejects_invalid_run_mode(self, mock_store):
        mock_store.create_job.return_value = 42
        mock_store.append_event = MagicMock()

        resp = client.post(
            "/api/upload_script",
            data={"topic": "T", "run_mode": "../../bad"},
            files={"file": ("story.txt", b"hello story", "text/plain")},
        )

        assert resp.status_code == 400
        assert "run_mode must be one of" in resp.json()["message"]
        mock_store.create_job.assert_not_called()

    @patch("utils.local_ui.job_store")
    def test_upload_script_rejects_invalid_boolean_form_field(self, mock_store):
        mock_store.create_job.return_value = 42
        mock_store.append_event = MagicMock()

        resp = client.post(
            "/api/upload_script",
            data={"topic": "T", "series": "not-a-bool"},
            files={"file": ("story.txt", b"hello story", "text/plain")},
        )

        assert resp.status_code == 400
        assert "series" in resp.json()["message"]
        mock_store.create_job.assert_not_called()


# -------------------- Artifacts Tests --------------------

class TestArtifactsEndpoint:
    def test_artifacts_empty_when_no_outputs(self):
        with patch("utils.local_ui.Path.exists", return_value=False):
            resp = client.get("/api/artifacts")
            assert resp.status_code == 200
            assert resp.json()["artifacts"] == []

    @patch("utils.local_ui.Path")
    def test_artifacts_returns_structured_data(self, mock_path):
        # Mock the output root directory
        mock_root = MagicMock()
        mock_root.exists.return_value = True
        mock_root.iterdir.return_value = []

        mock_path.return_value = mock_root
        mock_path.__truediv__.return_value = mock_root

        resp = client.get("/api/artifacts")
        assert resp.status_code == 200

    def test_artifact_detail_nonexistent(self):
        resp = client.get("/api/artifacts/nonexistent")
        assert resp.status_code == 404


# -------------------- Memory Tests --------------------

class TestMemoryEndpoint:
    def test_memory_empty_when_no_projects(self):
        with patch("utils.local_ui.Path.exists", return_value=False):
            resp = client.get("/api/memory")
            assert resp.status_code == 200
            assert resp.json()["memory"] == []


# -------------------- Characters Tests --------------------

class TestCharactersEndpoint:
    def test_characters_empty_when_no_projects(self):
        with patch("utils.local_ui.Path.exists", return_value=False):
            resp = client.get("/api/characters")
            assert resp.status_code == 200
            assert resp.json()["characters"] == []


# -------------------- A/B API Extension Tests --------------------

class TestABExtension:
    @patch("utils.local_ui.load_config")
    def test_ab_pick_returns_destination_paths(self, mock_load_config):
        from pathlib import Path as RealPath
        mock_load_config.return_value = {"image_gen": {"backend": "bonsai"}}

        # Create test dirs in real studio_outputs to avoid path resolution issues
        test_variant_dir = RealPath("studio_outputs") / "ab_test" / "testjob_abtest" / "variant_a"
        test_variant_dir.mkdir(parents=True, exist_ok=True)
        (test_variant_dir / "img_001.png").write_text("fake")

        from utils.local_ui import _ab_jobs, _ab_jobs_lock
        with _ab_jobs_lock:
            _ab_jobs["testjob_abtest"] = {
                "status": "ready",
                "images_a": [],
                "images_b": [],
                "segment_num": 1,
                "topic": "test_topic_ab",
                "error": None,
            }

        try:
            resp = client.post("/api/ab/pick", data={
                "job_id": "testjob_abtest",
                "choice": "a",
                "segment_num": 1,
            })
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.json()}"
            data = resp.json()
            assert data["status"] == "committed"
            assert data["choice"] == "a"
            assert len(data["images"]) > 0
        finally:
            with _ab_jobs_lock:
                _ab_jobs.pop("testjob_abtest", None)
            # Cleanup test dirs
            import shutil
            test_parent = RealPath("studio_outputs") / "ab_test" / "testjob_abtest"
            if test_parent.exists():
                shutil.rmtree(test_parent)
