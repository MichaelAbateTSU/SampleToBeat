"""Single-owner HTTPS hosting configuration and access boundary."""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


@dataclass(frozen=True)
class Hosting:
    origin: str
    username: str
    salt: bytes
    password_hash: bytes
    iterations: int

    @classmethod
    def from_env(cls) -> Hosting | None:
        mode = os.environ.get("CHOPLAB_MODE", "local")
        if mode not in ("local", "hosted"):
            raise ValueError("CHOPLAB_MODE must be local or hosted")
        if mode == "local":
            return None
        origin = os.environ.get("CHOPLAB_PUBLIC_URL", "").rstrip("/")
        url = urlsplit(origin)
        if (url.scheme != "https" or not url.hostname or url.path or url.query or url.fragment
                or url.username or url.password):
            raise ValueError("Hosted mode requires CHOPLAB_PUBLIC_URL=https://your-host (no path)")
        username = os.environ.get("CHOPLAB_USERNAME", "owner")
        if not username or ":" in username:
            raise ValueError("CHOPLAB_USERNAME must be nonempty and contain no colon")
        secret = os.environ.get("CHOPLAB_PASSWORD_HASH_FILE", "")
        if not secret:
            raise ValueError("Hosted mode requires CHOPLAB_PASSWORD_HASH_FILE")
        try:
            algorithm, rounds, salt, digest = Path(secret).read_text().strip().split("$")
            iterations = int(rounds)
            salt_bytes, hash_bytes = bytes.fromhex(salt), bytes.fromhex(digest)
            if algorithm != "pbkdf2_sha256" or not 600_000 <= iterations <= 2_000_000:
                raise ValueError()
            if len(salt_bytes) < 16 or len(hash_bytes) != 32:
                raise ValueError()
        except (OSError, ValueError) as e:
            raise ValueError("Invalid password hash file; run scripts/hosting_password.py") from e
        return cls(origin, username, salt_bytes, hash_bytes, iterations)


HOSTING = Hosting.from_env()


def listening_port() -> int:
    raw = os.environ.get("CHOPLAB_PORT") or (os.environ.get("PORT") if HOSTING else None) or "8765"
    try:
        port = int(raw)
    except ValueError:
        raise ValueError("ChopLab listening port must be an integer from 1 to 65535.") from None
    if not 1 <= port <= 65535:
        raise ValueError("ChopLab listening port must be an integer from 1 to 65535.")
    return port


class OwnerAccess:
    def __init__(self, app: ASGIApp, config: Hosting | None = HOSTING):
        self.app, self.config = app, config
        self._accepted: bytes | None = None
        self._checking = asyncio.Lock()
        self._next_attempt = 0.0

    def _verify(self, authorization: str) -> bool:
        try:
            scheme, encoded = authorization.split(" ", 1)
            if scheme.lower() != "basic":
                return False
            username, password = base64.b64decode(encoded, validate=True).decode("utf-8").split(":", 1)
        except (ValueError, UnicodeError, binascii.Error):
            return False
        config = self.config
        assert config is not None
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), config.salt, config.iterations)
        return hmac.compare_digest(username.encode(), config.username.encode()) and hmac.compare_digest(digest, config.password_hash)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        config = self.config
        if config is None or scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        # Only the data-free readiness endpoint is anonymous.
        if scope["path"] != "/api/health":
            if scope.get("scheme") != "https":
                await JSONResponse({"detail": "HTTPS is required for owner access."}, 400)(scope, receive, send)
                return
            origin = headers.get("origin")
            if ((origin is not None and origin != config.origin)
                    or headers.get("sec-fetch-site") == "cross-site"):
                await JSONResponse({"detail": "Cross-site access is not allowed."}, 403)(scope, receive, send)
                return
            authorization = headers.get("authorization", "")
            fingerprint = hashlib.sha256(authorization.encode()).digest()
            accepted = self._accepted is not None and hmac.compare_digest(fingerprint, self._accepted)
            if not accepted and authorization and len(authorization) <= 4096:
                # Hash only one new credential per second. Cache only the successful
                # fingerprint so audio ranges and SSE do not repeatedly spend CPU.
                async with self._checking:
                    accepted = self._accepted is not None and hmac.compare_digest(fingerprint, self._accepted)
                    if not accepted:
                        now = asyncio.get_running_loop().time()
                        if now < self._next_attempt:
                            await JSONResponse({"detail": "Too many login attempts; wait a second."}, 429,
                                               headers={"Retry-After": "1"})(scope, receive, send)
                            return
                        self._next_attempt = now + 1
                        accepted = await asyncio.to_thread(self._verify, authorization)
                        if accepted:
                            self._accepted = fingerprint
            if not accepted:
                await JSONResponse({"detail": "Owner login required."}, 401,
                                   headers={"WWW-Authenticate": 'Basic realm="ChopLab", charset="UTF-8"'})(scope, receive, send)
                return

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend([
                    (b"cache-control", b"private, no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"same-origin"),
                    (b"x-frame-options", b"DENY"),
                ])
            await send(message)

        await self.app(scope, receive, secure_send)
