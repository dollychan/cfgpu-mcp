from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import aiohttp

logger = logging.getLogger(__name__)

from cfgpu_mcp.context import get_request_token
from cfgpu_mcp.errors import CFGPUError

DEFAULT_BASE_URL = "https://www.cfgpu.com/userapi/v1"

# Total seconds a single request may take before aiohttp aborts it. Sync image
# models return the generated result in the POST body, so this must be generous;
# async POST and poll GET both complete well within it. Set via config.yaml
# (cfgpu_api.http_timeout); the server passes it through get_client().
DEFAULT_HTTP_TIMEOUT = 120.0
DEFAULT_CONNECT_TIMEOUT = 10.0

# What we advertise on every request, deliberately excluding `br` / `zstd`.
#
# aiohttp derives Accept-Encoding from whichever optional codecs happen to be
# importable on the host (`Brotli`, `zstandard`), so the wire format silently
# depends on the deployment's transitive dependencies — and if that decode then
# fails, aiohttp raises ContentEncodingError("Can not decode content-encoding:
# br"), which surfaces as a bogus "网络请求失败" on an otherwise healthy poll.
# The case that surfaced it was a Cloudflare-fronted provider (the since-retired
# `submodel` host), which brotli-encodes JSON as soon as the client offers `br`.
# That provider is gone; the pin is not host-specific and stays.
#
# Every body here is small JSON (a task envelope, a URL); gzip already covers it
# and is stdlib-backed, so pinning the set costs nothing and makes the wire
# format identical on every host.
ACCEPT_ENCODING = "gzip, deflate"


class CFGPUClient:
    def __init__(
        self,
        api_token: str | None = None,
        base_url: str | None = None,
        http_timeout: float | None = None,
        connect_timeout: float | None = None,
        auth_scheme: str = "bearer",
        token_env: str = "CFGPU_API_TOKEN",
        use_request_token: bool = True,
        provider: str = "cfgpu",
    ) -> None:
        # No raise here: in HTTP multi-tenant mode the token arrives per request
        # (ContextVar). ``api_token`` is only a fallback (stdio / direct use).
        self._token = api_token or os.environ.get(token_env)
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        # "bearer" → `Authorization: Bearer <t>`; "raw" → `Authorization: <t>`.
        # Not every upstream speaks Bearer: the co-located comfy-gateway matches
        # the reverse proxy already on that machine, which takes a bare token.
        self._auth_scheme = auth_scheme
        self._token_env = token_env
        # Whether the per-request ContextVar token (the *caller's* CFGPU
        # credential, from the Authorization header in multi-tenant HTTP mode) may
        # be used for this upstream. True only for CFGPU itself. Any other
        # provider is a different host with a different trust relationship, and
        # forwarding a tenant's CFGPU token there would hand a third party a
        # credential that was issued for CFGPU (comfy-gateway API.md §1 / D1-d).
        self._use_request_token = use_request_token
        self._provider = provider
        # Distinguish "not supplied" (None) from a caller-provided value. settings
        # has already validated its values as positive, so we don't re-coerce 0 here.
        self._timeout = aiohttp.ClientTimeout(
            total=http_timeout if http_timeout is not None else DEFAULT_HTTP_TIMEOUT,
            connect=connect_timeout if connect_timeout is not None else DEFAULT_CONNECT_TIMEOUT,
        )
        self._session: aiohttp.ClientSession | None = None

    @property
    def base_url(self) -> str:
        return self._base_url

    def _resolve_token(self) -> str:
        """Token precedence: request ContextVar > constructor/env fallback.

        The shared session carries no auth header, so the token is injected per
        request — one connection pool serves every tenant.

        For a non-cfgpu provider the ContextVar is skipped entirely (see
        ``use_request_token``) and the only source is the provider's own
        ``token_env``, which ``_parse_providers`` has already refused to let be
        ``CFGPU_API_TOKEN``. So there is no path — not even a fallback one — by
        which a caller's CFGPU credential reaches another host.
        """
        token = (get_request_token() if self._use_request_token else None) or self._token
        if not token:
            if self._use_request_token:
                msg = "缺少 CFGPU API Token：请在请求头携带 Authorization，或设置 CFGPU_API_TOKEN。"
            else:
                msg = (
                    f"provider {self._provider!r} 缺少凭据：请设置环境变量 {self._token_env}。"
                    f"（该 provider 不使用请求头里的用户 token，也不回退到 CFGPU_API_TOKEN。）"
                )
            raise CFGPUError(error_type="auth", user_message=msg, original={})
        return token

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def post(self, path: str, json: dict) -> dict:
        return await self._request("POST", path, json=json)

    async def get(self, path: str) -> dict:
        return await self._request("GET", path)

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self._base_url}/{path.lstrip('/')}"
        if method == "POST" and os.getenv("CFGPU_DRY_RUN"):
            logger.info("DRY-RUN POST %s\n%s", url, _json.dumps(kwargs.get("json", {}), ensure_ascii=False, indent=2))
        token = self._resolve_token()
        headers = {
            "Authorization": token if self._auth_scheme == "raw" else f"Bearer {token}",
            "Accept-Encoding": ACCEPT_ENCODING,
        }
        session = await self._get_session()
        try:
            async with session.request(method, url, headers=headers, **kwargs) as resp:
                body: dict = {}
                try:
                    body = await resp.json(content_type=None)
                except Exception:
                    body = {"message": await resp.text()}

                if not isinstance(body, dict):
                    body = {"_raw": body}

                if not resp.ok or (isinstance(body.get("error"), dict) and body["error"]):
                    raise CFGPUError.from_http_response(resp.status, body)
                # Full response logging — DEBUG by default; INFO (pretty-printed)
                # when CFGPU_LOG_RESPONSES is set, to verify adapter / card.md.
                if os.getenv("CFGPU_LOG_RESPONSES"):
                    logger.info(
                        "CFGPU response [%s %s]:\n%s",
                        method, url, _json.dumps(body, ensure_ascii=False, indent=2),
                    )
                else:
                    logger.debug("CFGPU response [%s %s]: %s", method, url, _json.dumps(body, ensure_ascii=False))
                return body
        except CFGPUError:
            raise
        except asyncio.TimeoutError as e:
            raise CFGPUError(
                error_type="timeout",
                user_message=f"请求超时（{self._timeout.total}s），请稍后重试或在 config.yaml 增大 cfgpu_api.http_timeout。",
                original={"url": url, "timeout": self._timeout.total},
                retryable=True,
            ) from e
        except aiohttp.ClientError as e:
            raise CFGPUError(
                error_type="unknown",
                user_message=f"网络请求失败：{e}",
                original={"error": str(e)},
            ) from e

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> "CFGPUClient":
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()
