import socket
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.url_security import build_validated_url, validate_service_base_url


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
