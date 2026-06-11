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


class CFGPUClient:
    def __init__(
        self,
        api_token: str | None = None,
        base_url: str | None = None,
        http_timeout: float | None = None,
        connect_timeout: float | None = None,
    ) -> None:
        # No raise here: in HTTP multi-tenant mode the token arrives per request
        # (ContextVar). ``api_token`` is only a fallback (stdio / direct use).
        self._token = api_token or os.environ.get("CFGPU_API_TOKEN")
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
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
        """
        token = get_request_token() or self._token
        if not token:
            raise CFGPUError(
                error_type="auth",
                user_message="缺少 CFGPU API Token：请在请求头携带 Authorization，或设置 CFGPU_API_TOKEN。",
                original={},
            )
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
        headers = {"Authorization": f"Bearer {self._resolve_token()}"}
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
