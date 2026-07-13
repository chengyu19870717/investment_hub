from __future__ import annotations

import os
import ssl

try:
    import certifi
except ImportError:  # pragma: no cover
    certifi = None

_CA_ENV = "WECHAT_ASSISTANT_CA_FILE"
_FALLBACK_CA_PATHS = (
    "/etc/ssl/cert.pem",
    "/opt/homebrew/etc/openssl@3/cert.pem",
    "/usr/local/etc/openssl@3/cert.pem",
)


def build_ssl_context() -> ssl.SSLContext:
    cafile = os.getenv(_CA_ENV, "").strip()
    if not cafile and certifi is not None:
        cafile = certifi.where()
    if not cafile:
        for candidate in _FALLBACK_CA_PATHS:
            if os.path.exists(candidate):
                cafile = candidate
                break
    if cafile and os.path.exists(cafile):
        return ssl.create_default_context(cafile=cafile)
    return ssl.create_default_context()
