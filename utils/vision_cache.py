"""VisionCache -- compound cache key (topic + content + config + prompt hash)."""

import hashlib
import json
import logging
import threading
import time
from pathlib import Path

import yaml

CACHE_VERSION = 2
log = logging.getLogger(__name__)

# Absolute default config path — resolves to <repo_root>/config/config.yaml
# regardless of the working directory at import time.
_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"


class VisionCache:
    def __init__(
        self,
        cache_dir="cache",
        config_path=None,
        prompts_path="prompts.yaml",
        prompt_key="analyze_story",
        force_refresh=False,
        max_entries=200,
    ):
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cache = self._dir / "vision_cache.json"
        self._meta = self._dir / "vision_cache_meta.json"
        # P2-12 fix: use absolute default so "config.yaml" (cwd-relative) never silently
        # resolves to a non-existent path and returns "noconfig" for every key.
        if config_path is None:
            self._cfg_path = _DEFAULT_CONFIG_PATH
        else:
            self._cfg_path = Path(config_path)
        self._prp_path = Path(prompts_path) if prompts_path else None
        self._pk = prompt_key
        self._fr = force_refresh
        self._max = max_entries
        self._lock = threading.Lock()
        self._data = {}
        self._meta_data = {}
        self._cfg_hash_cache = None
        self._prp_hash_cache = None
        self._cfg_mtime = 0
        self._prp_mtime = 0
        for p in [self._cache, self._meta]:
            if p.exists():
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    d = {}
                if p == self._cache:
                    self._data = d
                else:
                    self._meta_data = d

    def _config_hash(self):
        if not self._cfg_path or not self._cfg_path.exists():
            log.warning(
                f"[VisionCache] Config path not found: {self._cfg_path} — "
                "cache key will use 'noconfig'; edit config_path to enable config-aware caching"
            )
            return "noconfig"
        mtime = self._cfg_path.stat().st_mtime
        if self._cfg_hash_cache and mtime == self._cfg_mtime:
            return self._cfg_hash_cache
        with open(self._cfg_path, encoding="utf-8") as f:
            c = yaml.safe_load(f) or {}
        r = {
            "models": c.get("models", {}),
            "ollama_host": c.get("ollama", {}).get("host"),
            "tts_lang": c.get("tts", {}).get("lang"),
        }
        self._cfg_hash_cache = hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest()[
            :16
        ]
        self._cfg_mtime = mtime
        return self._cfg_hash_cache

    def _prompt_hash(self):
        if not self._prp_path or not self._prp_path.exists():
            return "noprompts"
        mtime = self._prp_path.stat().st_mtime
        if self._prp_hash_cache and mtime == self._prp_mtime:
            return self._prp_hash_cache
        with open(self._prp_path, encoding="utf-8") as f:
            p = yaml.safe_load(f) or {}
        content = json.dumps(p.get(self._pk, ""), sort_keys=True)
        self._prp_hash_cache = hashlib.sha256(content.encode()).hexdigest()[:16]
        self._prp_mtime = mtime
        return self._prp_hash_cache

    def _key(self, topic, content_text: str = ""):
        th = hashlib.sha256(topic.strip().encode()).hexdigest()[:12]
        # P2-12 fix: include content_text hash so an edited story file (same topic)
        # produces a different key and doesn't serve a stale vision doc.
        ct_hash = hashlib.sha256((content_text or "").encode()).hexdigest()[:12]
        return f"{th}:{ct_hash}:{self._config_hash()}:{self._prompt_hash()}"

    def get(self, topic, content_text: str = ""):
        if self._fr:
            return None
        with self._lock:
            k = self._key(topic, content_text)
            e = self._data.get(k)
            ev = self._meta_data.get(k, {}).get("cache_version", 1)
            if e is None:
                return None
            if ev != CACHE_VERSION:
                return None
            return e

    def set(self, topic, vision_doc, content_text: str = ""):
        with self._lock:
            k = self._key(topic, content_text)
            self._data[k] = vision_doc
            self._meta_data[k] = {
                "cache_version": CACHE_VERSION,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "topic": topic[:60],
            }
            if len(self._data) > self._max:
                oldest = sorted(
                    self._meta_data, key=lambda x: self._meta_data[x].get("created_at", "")
                )[: max(1, self._max // 5)]
                for ok in oldest:
                    self._data.pop(ok, None)
                    self._meta_data.pop(ok, None)
            try:
                tmp_cache = self._cache.with_suffix(".tmp")
                tmp_meta = self._meta.with_suffix(".tmp")
                tmp_cache.write_text(
                    json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                tmp_meta.write_text(
                    json.dumps(self._meta_data, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                import os

                os.replace(str(tmp_cache), str(self._cache))
                os.replace(str(tmp_meta), str(self._meta))
            except Exception as e:
                log.warning(f"VisionCache save failed: {e}")
