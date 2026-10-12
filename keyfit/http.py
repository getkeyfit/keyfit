"""TLS for Keyfit's own HTTPS checks.

The python.org build of Python ships without a CA bundle of its own, so in a clean
terminal every HTTPS check would fail with CERTIFICATE_VERIFY_FAILED. Superset
terminals happen to set SSL_CERT_FILE, which hid this. When the default context
has no CA certificates, use the bundle macOS maintains at /etc/ssl/cert.pem.
"""

from __future__ import annotations

import functools
import os
import ssl

MACOS_BUNDLE = "/etc/ssl/cert.pem"


@functools.lru_cache(maxsize=1)
def context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca", 0) == 0 and not os.environ.get("SSL_CERT_DIR") \
            and os.path.exists(MACOS_BUNDLE):
        ctx = ssl.create_default_context(cafile=MACOS_BUNDLE)
    return ctx
