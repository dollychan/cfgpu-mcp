from __future__ import annotations

import json
import logging
import os
import aiohttp

logger = logging.getLogger(__name__)

from cfgpu_mcp.errors import CFGPUError

DEFAULT_BASE_URL = "https://www.cfgpu.com/userapi/v1"


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
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self._token}"},
            )
        return self._session

    async def post(self, path: str, json: dict) -> dict:
        return await self._request("POST", path, json=json)

    async def get(self, path: str) -> dict:
        return await self._request("GET", path)

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self._base_url}/{path.lstrip('/')}"
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
                logger.debug("CFGPU response [%s %s]: %s", method, url, json.dumps(body, ensure_ascii=False))
                return body
        except CFGPUError:
            raise
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
