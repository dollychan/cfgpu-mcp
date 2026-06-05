from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import aiohttp

logger = logging.getLogger(__name__)

from cfgpu_mcp.errors import CFGPUError


def _env_float(name: str, default: float) -> float:
    """Read a positive float from env; fall back to default on missing/invalid."""
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        val = float(raw)
        return val if val > 0 else default
    except ValueError:
        return default

DEFAULT_BASE_URL = "https://www.cfgpu.com/userapi/v1"

# Total seconds a single request may take before aiohttp aborts it. Sync image
# models return the generated result in the POST body, so this must be generous;
# async POST and poll GET both complete well within it. Override via env.
DEFAULT_HTTP_TIMEOUT = 120.0
DEFAULT_CONNECT_TIMEOUT = 10.0


class CFGPUClient:
    def __init__(
        self,
        api_token: str | None = None,
        base_url: str | None = None,
    ) -> None:
        token = api_token or os.environ.get("CFGPU_API_TOKEN")
        if not token:
            from cfgpu_mcp.errors import CFGPUError
            raise CFGPUError(
                error_type="auth",
                user_message="CFGPU_API_TOKEN 未设置，请在环境变量中配置 API Token。",
                original={},
            )
        self._token = token
        self._base_url = (base_url or os.getenv("CFGPU_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self._timeout = aiohttp.ClientTimeout(
            total=_env_float("CFGPU_HTTP_TIMEOUT", DEFAULT_HTTP_TIMEOUT),
            connect=_env_float("CFGPU_CONNECT_TIMEOUT", DEFAULT_CONNECT_TIMEOUT),
        )
        self._session: aiohttp.ClientSession | None = None

    @property
    def base_url(self) -> str:
        return self._base_url

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            )
        return self._session

    async def post(self, path: str, json: dict) -> dict:
        return await self._request("POST", path, json=json)

    async def get(self, path: str) -> dict:
        return await self._request("GET", path)

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self._base_url}/{path.lstrip('/')}"
        if method == "POST" and os.getenv("CFGPU_DRY_RUN"):
            logger.info("DRY-RUN POST %s\n%s", url, _json.dumps(kwargs.get("json", {}), ensure_ascii=False, indent=2))
        session = await self._get_session()
        try:
            async with session.request(method, url, **kwargs) as resp:
                body: dict = {}
                try:
                    body = await resp.json(content_type=None)
                except Exception:
                    body = {"message": await resp.text()}

                if not isinstance(body, dict):
                    body = {"_raw": body}

                if not resp.ok or (isinstance(body.get("error"), dict) and body["error"]):
                    raise CFGPUError.from_http_response(resp.status, body)
                logger.debug("CFGPU response [%s %s]: %s", method, url, _json.dumps(body, ensure_ascii=False))
                return body
        except CFGPUError:
            raise
        except asyncio.TimeoutError as e:
            raise CFGPUError(
                error_type="timeout",
                user_message=f"请求超时（{self._timeout.total}s），请稍后重试或增大 CFGPU_HTTP_TIMEOUT。",
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
