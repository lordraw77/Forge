"""Corporate egress: proxy variables reach LiteLLM's transport; extra CA joins the bundle."""

from __future__ import annotations

import http.client
import os
import threading
from collections.abc import Iterator
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from forge.core import network
from forge.gateway.adapter import LiteLLMAdapter
from tests.support.mock_provider import MockProvider
from tests.unit.test_gateway import make_model


class _Proxy(BaseHTTPRequestHandler):
    """Minimal forward proxy for plain HTTP: records and forwards absolute-URI requests."""

    seen: list[str] = []  # noqa: RUF012 - shared by the handler instances

    def _forward(self) -> None:
        url = urlsplit(self.path)
        _Proxy.seen.append(self.path)
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        conn = http.client.HTTPConnection(url.hostname or "", url.port or 80, timeout=10)
        headers = {k: v for k, v in self.headers.items() if k.lower() != "proxy-connection"}
        conn.request(self.command, url.path + (f"?{url.query}" if url.query else ""), body, headers)
        resp = conn.getresponse()
        data = resp.read()
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(k, v)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = _forward

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def proxy() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Proxy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Proxy.seen.clear()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


async def test_litellm_calls_go_through_the_egress_proxy(
    proxy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with MockProvider() as base_url:
        for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "NO_PROXY", "no_proxy"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("HTTP_PROXY", proxy)
        monkeypatch.delenv("AIOHTTP_TRUST_ENV", raising=False)
        applied = network.configure_outbound()
        assert applied["proxy"] == "environment"
        assert os.environ["AIOHTTP_TRUST_ENV"] == "True"
        base = make_model("m", model_id="via-proxy")
        model = replace(base, provider=replace(base.provider, base_url=base_url))
        result = await LiteLLMAdapter().chat(
            model, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}
        )
        assert "pong" in result["choices"][0]["message"]["content"]
        assert any(p.endswith("/v1/chat/completions") for p in _Proxy.seen), _Proxy.seen


def test_extra_ca_is_appended_and_exported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pem = "-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----\n"
    extra = tmp_path / "corp.pem"
    extra.write_text(pem)
    target = tmp_path / "bundle.pem"
    bundle = network.build_ca_bundle(str(extra), target)
    text = bundle.read_text()
    assert text.endswith(pem)
    assert text.count("BEGIN CERTIFICATE") > 10  # the default store is kept
    (tmp_path / "bad.pem").write_text("not a certificate")
    with pytest.raises(ValueError, match="no PEM"):
        network.build_ca_bundle(str(tmp_path / "bad.pem"), target)
    monkeypatch.setattr(network, "BUNDLE_PATH", target)
    monkeypatch.setenv("FORGE_EXTRA_CA_FILE", str(extra))
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    for var in network._CA_VARIABLES:
        monkeypatch.delenv(var, raising=False)
    applied = network.configure_outbound()
    assert applied == {"ca_bundle": str(target)}
    assert all(os.environ[v] == str(target) for v in network._CA_VARIABLES)
