"""test_source_loader_redirects.py - Tests for source-loader redirect handling."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.source_loader import SourceLoaderError, _load_url


class TestRedirectHandling:
    def test_metadata_redirect_rejected(self):
        """Redirect to metadata IP (169.254.169.254) should be rejected."""
        mock_resp_initial = MagicMock()
        mock_resp_initial.status_code = 302
        mock_resp_initial.is_redirect = True
        mock_resp_initial.headers = {"Location": "http://169.254.169.254/latest/meta-data/"}
        mock_resp_initial.text = ""
        mock_resp_initial.url = "https://example.com/start"

        mock_resp_final = MagicMock()
        mock_resp_final.status_code = 200
        mock_resp_final.is_redirect = False
        mock_resp_final.text = "<html><body>content</body></html>"
        mock_resp_final.url = "https://example.com/end"

        def mock_validate(url):
            if "169.254.169.254" in url:
                raise ValueError("URL host is disallowed: 169.254.169.254")
            return url

        with (
            patch("requests.get", side_effect=[mock_resp_initial, mock_resp_final]),
            patch("utils.url_security.validate_source_url", side_effect=mock_validate),
            patch.dict("sys.modules", {"trafilatura": MagicMock(extract=lambda *a, **k: "content")}),
        ):
            with pytest.raises(ValueError, match="URL host is disallowed"):
                _load_url("https://example.com/start", None)

    def test_relative_redirect_resolved_correctly(self):
        """Relative redirect should be resolved against current URL."""
        mock_resp_initial = MagicMock()
        mock_resp_initial.status_code = 302
        mock_resp_initial.is_redirect = True
        mock_resp_initial.headers = {"Location": "/new-page"}
        mock_resp_initial.text = ""
        mock_resp_initial.url = "https://example.com/old-page"

        mock_resp_final = MagicMock()
        mock_resp_final.status_code = 200
        mock_resp_final.is_redirect = False
        mock_resp_final.text = "<html><body>redirected content</body></html>"
        mock_resp_final.url = "https://example.com/new-page"

        with (
            patch("requests.get", side_effect=[mock_resp_initial, mock_resp_final]),
            patch("utils.url_security.validate_source_url", return_value="https://example.com/new-page"),
            patch.dict("sys.modules", {"trafilatura": MagicMock(extract=lambda *a, **k: "redirected content")}),
        ):
            doc = _load_url("https://example.com/old-page", None)
            assert doc.text == "redirected content"
            assert doc.metadata["final_url"] == "https://example.com/new-page"

    def test_redirect_chain_limit_enforced(self):
        """Redirect chain limit should raise SourceLoaderError."""
        mock_resp = MagicMock()
        mock_resp.status_code = 302
        mock_resp.is_redirect = True
        mock_resp.headers = {"Location": "/next"}
        mock_resp.text = ""
        mock_resp.url = "https://example.com/current"

        with (
            patch("requests.get", return_value=mock_resp),
            patch("utils.url_security.validate_source_url", return_value="https://example.com/next"),
            patch.dict("sys.modules", {"trafilatura": MagicMock(extract=lambda *a, **k: "content")}),
        ):
            with pytest.raises(SourceLoaderError, match="too many redirects"):
                _load_url("https://example.com/start", None)

    def test_missing_location_rejected(self):
        """Missing Location header should raise error."""
        mock_resp = MagicMock()
        mock_resp.status_code = 302
        mock_resp.is_redirect = True
        mock_resp.headers = {}  # No Location header
        mock_resp.text = ""
        mock_resp.url = "https://example.com/start"

        with (
            patch("requests.get", return_value=mock_resp),
            patch.dict("sys.modules", {"trafilatura": MagicMock(extract=lambda *a, **k: "content")}),
        ):
            with pytest.raises(SourceLoaderError, match="redirect missing Location"):
                _load_url("https://example.com/start", None)
