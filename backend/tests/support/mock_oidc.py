"""Minimal OpenID Connect provider for tests, on a real port in a thread.

`with MockOidc() as idp:` gives `idp.issuer`. `idp.code(**claims)` registers an authorization
code whose ID token carries those claims; keyword `_nonce`, `_aud`, `_exp`, `_iss` and
`_sign_with_other_key` override the corresponding protocol fields to build bad tokens.
"""

from __future__ import annotations

import socket
import threading
import time
import uuid
from typing import Any

import uvicorn
from fastapi import FastAPI, Form
from fastapi.responses import JSONResponse
from joserfc import jwt
from joserfc.jwk import RSAKey


class MockOidc:
    def __init__(self, client_id: str = "forge-test") -> None:
        self.client_id = client_id
        self.key = RSAKey.generate_key(2048, {"kid": "k1", "use": "sig"})
        self.other_key = RSAKey.generate_key(2048, {"kid": "k1", "use": "sig"})
        self.codes: dict[str, dict[str, Any]] = {}
        self.last_exchange: dict[str, str] = {}
        self.issuer = ""

    def code(self, nonce: str, **claims: Any) -> str:
        code = uuid.uuid4().hex
        self.codes[code] = {"nonce": nonce, **claims}
        return code

    def _token(self, spec: dict[str, Any]) -> str:
        now = int(time.time())
        spec = dict(spec)
        key = self.other_key if spec.pop("_sign_with_other_key", False) else self.key
        claims: dict[str, Any] = {
            "iss": spec.pop("_iss", self.issuer),
            "aud": spec.pop("_aud", self.client_id),
            "exp": spec.pop("_exp", now + 300),
            "iat": now,
            "sub": spec.pop("sub", "subject-1"),
            "nonce": spec.pop("_nonce", spec.pop("nonce", "")),
        }
        spec.pop("nonce", None)
        claims.update(spec)
        return jwt.encode({"alg": "RS256", "kid": "k1"}, claims, key)

    def build(self) -> FastAPI:
        app = FastAPI()

        @app.get("/.well-known/openid-configuration")
        def discovery() -> dict[str, str]:
            return {
                "issuer": self.issuer,
                "authorization_endpoint": f"{self.issuer}/authorize",
                "token_endpoint": f"{self.issuer}/token",
                "jwks_uri": f"{self.issuer}/jwks",
            }

        @app.get("/jwks")
        def jwks() -> dict[str, Any]:
            return {"keys": [self.key.as_dict(private=False)]}

        @app.post("/token")
        def token(
            code: str = Form(),
            client_id: str = Form(),
            client_secret: str = Form(),
            code_verifier: str = Form(),
            redirect_uri: str = Form(),
            grant_type: str = Form(),
        ) -> JSONResponse:
            self.last_exchange = {
                "client_id": client_id,
                "client_secret": client_secret,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
                "grant_type": grant_type,
            }
            spec = self.codes.pop(code, None)
            if spec is None:
                return JSONResponse({"error": "invalid_grant"}, 400)
            return JSONResponse({"id_token": self._token(spec), "token_type": "Bearer"})

        return app

    def __enter__(self) -> MockOidc:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        self.issuer = f"http://127.0.0.1:{port}"
        self._server = uvicorn.Server(
            uvicorn.Config(self.build(), host="127.0.0.1", port=port, log_level="warning")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started and time.time() < deadline:
            time.sleep(0.05)
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)
