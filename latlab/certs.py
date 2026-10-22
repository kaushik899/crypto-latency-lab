"""Trusted certificates everywhere, on every computer.

Python from python.org on macOS ships without the system's root certificates, so every HTTPS call fails
with CERTIFICATE_VERIFY_FAILED until "Install Certificates.command" is run. Pointing SSL_CERT_FILE at
certifi's bundle fixes aiohttp, ccxt and urllib in one place, before any connection is made.
"""

import os


def use_certifi() -> None:
    if os.environ.get("SSL_CERT_FILE"):
        return
    try:
        import certifi
    except ImportError:
        return
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
