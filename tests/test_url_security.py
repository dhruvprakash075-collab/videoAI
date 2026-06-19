import socket
import sys
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.url_security import (
    build_validated_url,
    safe_url_open,
    validate_local_service_base_url,
    validate_service_base_url,
)


def test_validate_service_base_url_rejects_metadata_ip():
    with pytest.raises(ValueError, match="disallowed"):
        validate_service_base_url("http://169.254.169.254/latest/meta-data")


def test_validate_service_base_url_rejects_file_scheme():
    with pytest.raises(ValueError, match="scheme"):
        validate_service_base_url("file:///etc/passwd")


def test_validate_service_base_url_allows_loopback_by_default():
    assert validate_service_base_url("http://localhost:11434") == "http://localhost:11434"


def test_validate_service_base_url_rejects_private_dns_when_not_loopback():
    info = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 80))]
    with patch("socket.getaddrinfo", return_value=info):
        with pytest.raises(ValueError, match="disallowed"):
            validate_service_base_url("https://example.test")


def test_build_validated_url_preserves_path_joining():
    assert build_validated_url("http://localhost:11434/", "/api/tags") == "http://localhost:11434/api/tags"


class TestSafeUrlOpen:
    def test_expected_content_type_accepted(self):
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "image/png"}
        mock_resp.read.return_value = b"fake image data"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp

        with (
            patch("urllib.request.build_opener", return_value=mock_opener),
            patch("utils.url_security.validate_source_url", return_value="https://example.com/image.png"),
        ):
            data = safe_url_open("https://example.com/image.png", expected_content_type="image/png")
            assert data == b"fake image data"

    def test_wrong_content_type_rejected(self):
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.read.return_value = b"<html></html>"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp

        with (
            patch("urllib.request.build_opener", return_value=mock_opener),
            patch("utils.url_security.validate_source_url", return_value="https://example.com/page"),
        ):
            with pytest.raises(ValueError, match="Unexpected content type"):
                safe_url_open("https://example.com/page", expected_content_type="image/png")

    def test_relative_redirect_resolved_before_validation(self):
        """Verify that safe_url_open's redirect handler validates the resolved absolute redirect URL."""
        captured_handlers = []

        def fake_build_opener(*handlers):
            captured_handlers.extend(handlers)

            opener = MagicMock()
            response = MagicMock()
            response.headers = {"Content-Type": "image/png"}
            response.read.return_value = b"png"
            response.close.return_value = None
            opener.open.return_value = response
            return opener

        with (
            patch("urllib.request.build_opener", side_effect=fake_build_opener),
            patch("utils.url_security.validate_source_url", return_value="https://example.com/start"),
        ):
            safe_url_open("https://example.com/start")

        redirect_handler = captured_handlers[0]()
        fake_req = urllib.request.Request("https://example.com/old/page")

        with patch("utils.url_security.validate_source_url") as validate_redirect:
            redirect_handler.redirect_request(
                fake_req,
                None,
                302,
                "Found",
                {},
                "/image.png",
            )

        validate_redirect.assert_called_with("https://example.com/image.png")


class TestValidateLocalServiceBaseUrl:
    def test_loopback_ip_allowed(self):
        assert validate_local_service_base_url("http://127.0.0.1:11434") == "http://127.0.0.1:11434"
        assert validate_local_service_base_url("http://[::1]:8188") == "http://[::1]:8188"

    def test_localhost_allowed(self):
        assert validate_local_service_base_url("http://localhost:11434") == "http://localhost:11434"

    def test_public_host_rejected(self):
        with pytest.raises(ValueError, match="must be loopback"):
            validate_local_service_base_url("https://example.com")

    def test_metadata_ip_rejected(self):
        with pytest.raises(ValueError, match="must be loopback"):
            validate_local_service_base_url("http://169.254.169.254/latest/meta-data")

    def test_private_non_loopback_rejected(self):
        with pytest.raises(ValueError, match="must be loopback"):
            validate_local_service_base_url("http://10.0.0.5:8080")

    def test_link_local_rejected(self):
        with pytest.raises(ValueError, match="must be loopback"):
            validate_local_service_base_url("http://169.254.1.1:8080")

    def test_file_scheme_rejected(self):
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            validate_local_service_base_url("file:///etc/passwd")

    def test_unsupported_scheme_rejected(self):
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            validate_local_service_base_url("ftp://localhost:21")
