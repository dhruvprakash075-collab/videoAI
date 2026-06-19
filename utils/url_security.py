from __future__ import annotations

import ipaddress
import logging
import socket
import urllib.request
from urllib.parse import urljoin, urlparse

log = logging.getLogger(__name__)


def _is_disallowed_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


def _host_resolves_to_disallowed_ip(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for _family, _socktype, _proto, _canonname, sockaddr in infos:
        ip: str = str(sockaddr[0])
        if _is_disallowed_ip(ip):
            return True
    return False


def _host_resolves_only_to_loopback(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(str(info[4][0])).is_loopback for info in infos)


def validate_service_base_url(raw_url: str, *, allow_loopback: bool = True) -> str:
    """Validate a URL for local service endpoints (Ollama, ComfyUI).

    When allow_loopback=True (default), localhost/127.0.0.1/::1 are permitted
    since Ollama and ComfyUI run locally. Private/metadata/link-local IPs
    beyond loopback are always rejected.
    """
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")
    if not parsed.hostname:
        raise ValueError("URL host is required")
    host = parsed.hostname
    if _is_disallowed_ip(host):
        if allow_loopback and ipaddress.ip_address(host).is_loopback:
            return raw_url.rstrip("/")
        raise ValueError(f"URL host is disallowed: {host}")
    if allow_loopback and host.lower() == "localhost" and _host_resolves_only_to_loopback(host):
        return raw_url.rstrip("/")
    if _host_resolves_to_disallowed_ip(host):
        raise ValueError(f"Resolved host is disallowed: {host}")
    return raw_url.rstrip("/")


def validate_local_service_base_url(raw_url: str) -> str:
    """Validate a URL for local-only service endpoints (Ollama, ComfyUI).

    Only loopback addresses (127.0.0.1, ::1, localhost) are allowed.
    Public hosts, metadata IPs, private non-loopback IPs, link-local IPs,
    file URLs, and unsupported schemes are all rejected.
    """
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")
    if not parsed.hostname:
        raise ValueError("URL host is required")
    host = parsed.hostname
    # Allow loopback IPs directly
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_loopback:
            return raw_url.rstrip("/")
        # Any non-loopback IP is rejected
        raise ValueError(f"URL host must be loopback for local service: {host}")
    except ValueError:
        pass
    # Allow localhost if it resolves only to loopback
    if host.lower() == "localhost" and _host_resolves_only_to_loopback(host):
        return raw_url.rstrip("/")
    # Everything else is rejected
    raise ValueError(f"URL host must be loopback for local service: {host}")


def validate_source_url(raw_url: str) -> str:
    """Validate a user-provided or source URL (e.g. from config or user input).

    Rejects localhost, loopback, private IPs, link-local, metadata IPs,
    file URLs, and unsupported schemes. Only http/https are allowed.
    Loopback is NEVER allowed for source URLs — these are external fetches.
    """
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme} (only http/https allowed)")
    if not parsed.hostname:
        raise ValueError("URL host is required")
    host = parsed.hostname
    # Block all loopback — source URLs must be external
    if _is_disallowed_ip(host):
        raise ValueError(f"URL host is disallowed: {host}")
    if host.lower() in ("localhost", "127.0.0.1", "::1", "[::1]"):
        raise ValueError(f"Source URL must not point to localhost: {host}")
    if _host_resolves_to_disallowed_ip(host):
        raise ValueError(f"Resolved host is disallowed: {host}")
    return raw_url.rstrip("/")


def build_validated_url(base_url: str, path: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def safe_url_open(
    url: str,
    *,
    timeout: int = 30,
    max_bytes: int = 10 * 1024 * 1024,
    user_agent: str = "Video.AI",
    expected_content_type: str | None = None,
) -> bytes:
    """Open a URL with SSRF validation, size cap, redirect control, and optional content-type check.

    Returns up to max_bytes of response body. Raises ValueError on
    disallowed hosts, redirects to private IPs, oversized responses,
    or content-type mismatch when expected_content_type is provided.
    """
    validated = validate_source_url(url)
    req = urllib.request.Request(validated, headers={"User-Agent": user_agent})
    # Disable automatic redirect handling so we can validate each hop
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            # Resolve relative redirect against current URL
            resolved = urljoin(req.full_url, newurl)
            # Validate resolved redirect target before following
            validate_source_url(resolved)
            return super().redirect_request(req, fp, code, msg, headers, resolved)

    opener = urllib.request.build_opener(_NoRedirect)
    resp = opener.open(req, timeout=timeout)
    try:
        # Check content type if expected
        if expected_content_type is not None:
            content_type = resp.headers.get("Content-Type", "")
            if content_type and not content_type.startswith(expected_content_type):
                raise ValueError(f"Unexpected content type: {content_type} (expected {expected_content_type})")
        data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f"Response too large: {len(data)} bytes (cap: {max_bytes})")
        return data
    finally:
        resp.close()
