"""OAuth access-token verification for the MCP resource server."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
import jwt
from jwt import InvalidTokenError, PyJWK
from mcp.server.auth.provider import AccessToken, TokenVerifier

logger = logging.getLogger(__name__)


class KeycloakJWTVerifier(TokenVerifier):
    """Validate Keycloak JWT access tokens against its JWKS endpoint."""

    def __init__(
        self,
        *,
        issuer: str,
        jwks_url: str,
        audience: str,
        required_scopes: list[str],
        algorithms: tuple[str, ...] = ("RS256",),
        cache_seconds: int = 300,
        clock_skew_seconds: int = 30,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.jwks_url = jwks_url
        self.audience = audience
        self.required_scopes = set(required_scopes)
        self.algorithms = algorithms
        self.cache_seconds = cache_seconds
        self.clock_skew_seconds = clock_skew_seconds
        self._jwks: dict[str, Any] | None = None
        self._jwks_expires_at = 0.0
        self._lock = asyncio.Lock()

    async def _fetch_jwks(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not force and self._jwks is not None and now < self._jwks_expires_at:
            return self._jwks

        async with self._lock:
            now = time.monotonic()
            if not force and self._jwks is not None and now < self._jwks_expires_at:
                return self._jwks

            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                response = await client.get(self.jwks_url)
                response.raise_for_status()
                payload = response.json()

            if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
                raise ValueError("OAuth JWKS response does not contain a keys array")

            self._jwks = payload
            self._jwks_expires_at = now + self.cache_seconds
            return payload

    @staticmethod
    def _find_key(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
        for key in jwks.get("keys", []):
            if isinstance(key, dict) and key.get("kid") == kid:
                return key
        return None

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            algorithm = header.get("alg")
            if not isinstance(kid, str) or algorithm not in self.algorithms:
                return None

            jwks = await self._fetch_jwks()
            jwk_data = self._find_key(jwks, kid)
            if jwk_data is None:
                jwks = await self._fetch_jwks(force=True)
                jwk_data = self._find_key(jwks, kid)
            if jwk_data is None:
                return None

            signing_key = PyJWK.from_dict(jwk_data, algorithm=algorithm).key
            claims = jwt.decode(
                token,
                key=signing_key,
                algorithms=list(self.algorithms),
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.clock_skew_seconds,
                options={"require": ["exp", "iat", "iss", "sub"]},
            )

            raw_scope = claims.get("scope", "")
            scopes = raw_scope.split() if isinstance(raw_scope, str) else []
            raw_scp = claims.get("scp")
            if isinstance(raw_scp, list):
                scopes.extend(str(value) for value in raw_scp)
            scopes = sorted(set(scopes))

            if not self.required_scopes.issubset(scopes):
                return None

            client_id = claims.get("azp") or claims.get("client_id") or "unknown"
            expires_at = claims.get("exp")
            return AccessToken(
                token=token,
                client_id=str(client_id),
                scopes=scopes,
                expires_at=int(expires_at) if isinstance(expires_at, (int, float)) else None,
                resource=self.audience,
                subject=str(claims.get("sub")),
                claims=claims,
            )
        except (InvalidTokenError, httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning("OAuth token verification failed: %s", exc)
            return None
