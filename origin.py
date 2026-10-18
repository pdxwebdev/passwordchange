"""
YadaCoin Open Source License (YOSL) v1.1

Copyright (c) 2017-2025 Matthew Vogel, Reynold Vogel, Inc.
"""

from urllib.parse import urlparse


def origin_key(url: str) -> str:
    """Normalize a URL to scheme://host:port for workflow lookup."""
    if not url or not str(url).strip():
        raise ValueError("url is required")
    parsed = urlparse(str(url).strip())
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("url must include scheme and host")
    host = parsed.hostname
    if not host:
        raise ValueError("url host required")
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError("url scheme must be http or https")
    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80
    return f"{scheme}://{host.lower()}:{port}"
