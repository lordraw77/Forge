"""Outbound network for corporate environments: egress proxy and extra trusted CA.

Deployment wiring (ADR-007), read once at process start:

- `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` (standard variables, also lower case). Compose and
  Helm add Forge's own services to `NO_PROXY`. httpx, requests, botocore, Hugging Face and
  LiteLLM 1.105 honour them (`AIOHTTP_TRUST_ENV` is also set, for LiteLLM's aiohttp transport
  in versions where it is off by default). Tested in `tests/unit/test_network.py`.
- `FORGE_EXTRA_CA_FILE`: PEM file with the corporate root(s), e.g. for a TLS-inspecting proxy.
  It is appended to the default bundle and the result is exported through the variables the
  libraries read (`SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `AWS_CA_BUNDLE`, `CURL_CA_BUNDLE`).
"""

from __future__ import annotations

import os
from pathlib import Path

BUNDLE_PATH = Path("/tmp/forge-ca-bundle.pem")  # noqa: S108 - container-local, read-only root
_CA_VARIABLES = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "AWS_CA_BUNDLE", "CURL_CA_BUNDLE")


_PROXY_VARIABLES = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")


def proxy_configured() -> bool:
    return any(os.environ.get(v) for v in _PROXY_VARIABLES)


def build_ca_bundle(extra_ca_file: str, target: Path | None = None) -> Path:
    """Default trust store + the extra CA(s); raises if the extra file holds no certificate."""
    import certifi

    target = target or BUNDLE_PATH
    extra = Path(extra_ca_file).read_text()
    if "-----BEGIN CERTIFICATE-----" not in extra:
        raise ValueError(f"{extra_ca_file} contains no PEM certificate")
    base = Path(certifi.where()).read_text()
    target.write_text(base.rstrip() + "\n" + extra.strip() + "\n")
    target.chmod(0o644)
    return target


def configure_outbound() -> dict[str, str]:
    """Apply the proxy and CA wiring to this process; returns what was set (for logging)."""
    applied: dict[str, str] = {}
    # Images with embedded model files never reach out for models or tokenizers.
    fetching = os.environ.get("FORGE_MODELS_FETCH") == "1"  # `forge models fetch` downloads
    if os.environ.get("FORGE_MODELS_MODE") == "embedded" and not fetching:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        applied["models"] = "embedded (offline)"
    extra = os.environ.get("FORGE_EXTRA_CA_FILE")
    if extra:
        bundle = build_ca_bundle(extra)
        for var in _CA_VARIABLES:
            os.environ[var] = str(bundle)
        applied["ca_bundle"] = str(bundle)
    if proxy_configured():
        # Explicit for LiteLLM's aiohttp transport (default on in 1.105, off in older versions).
        os.environ["AIOHTTP_TRUST_ENV"] = "True"
        applied["proxy"] = "environment"
        applied["no_proxy"] = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    return applied
