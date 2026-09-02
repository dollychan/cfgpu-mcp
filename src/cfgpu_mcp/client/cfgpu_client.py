from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import time

import aiohttp

from cfgpu_mcp.context import get_request_token
from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.settings import DEFAULT_PROVIDER

logger = logging.getLogger(__name__)

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

    def _timeout_setting_path(self, field: str = "http_timeout") -> str:
        """The config.yaml key that actually governs *this* client's ``field``.

        Only the built-in ``cfgpu`` provider reads ``cfgpu_api.*``. Every other
        provider carries its own, and ``config.get_client`` prefers it whenever it
        is set — so naming the top-level key in a non-cfgpu timeout sends the
        reader to a knob that changes nothing for the model that just failed. They
        raise it, retry, time out identically, and now distrust the message. Same
        discipline as ``card_hint``: a remedy that cannot work is worse than no
        remedy.
        """
        if self._provider == DEFAULT_PROVIDER:
            return f"cfgpu_api.{field}"
        return f"providers.{self._provider}.{field}"

    def _timeout_error(
        self, exc: BaseException, url: str, elapsed: float, method: str = "GET"
    ) -> CFGPUError:
        """Turn an aiohttp timeout into an error that names the phase that failed.

        ``aiohttp.ConnectionTimeoutError`` — DNS, TCP and TLS, i.e. everything
        before a byte is sent — subclasses ``asyncio.TimeoutError`` just like the
        total-budget timeout does, so one handler catches both. Reporting both
        with ``timeout.total`` printed a number that had not elapsed and pointed
        at ``http_timeout``, a knob that cannot move a connect failure: raised
        120 → 300, retried, failed in the same 10 seconds, and the message was
        now twice wrong. (2026-09-02, MiniMax-H3 on cfgpu-daily: the server could
        not reach the daily host at all; the log gap was exactly
        ``connect_timeout``.) Same discipline as ``_timeout_setting_path``.

        ``elapsed`` is measured, not read off the config — it is the one number
        that distinguishes the two cases without trusting this classification.

        ``method`` and the phase together decide ``retryable``, which at the agent
        boundary means one specific thing: *is resending this request safe?*

        - **connect phase** — DNS/TCP/TLS never completed, so the upstream received
          nothing whatever the verb was. Safe to resend.
        - **request phase on a GET** — polling is idempotent; asking again costs
          nothing and creates nothing.
        - **request phase on a POST** — the bytes went out and the answer did not
          come back. Upstream may have accepted the job and started billing, and
          because ``TaskManager.create`` POSTs *before* ``insert_task``, there is
          neither a returned task_id nor a local row to reconcile against. Resending
          is a coin flip on a double charge, so this is the one timeout that is not
          retryable — and the one that sets ``outcome_unknown``, because "did this
          take effect?" genuinely has no answer here.
        """
        connect_phase = isinstance(exc, aiohttp.ConnectionTimeoutError)
        budget = self._timeout.connect if connect_phase else self._timeout.total
        field = "connect_timeout" if connect_phase else "http_timeout"
        if connect_phase:
            detail = (
                f"{elapsed:.1f}s 内没能与 provider {self._provider!r} 建立连接"
                f"（DNS / TCP / TLS 都在这一步，上游还没收到任何请求）。"
                "这通常是这台机器到该上游的网络不通，而不是上游慢 —— "
                f"增大 {self._timeout_setting_path()} 不会有任何作用。"
                f"请先确认本机能否访问 {self._base_url}，再考虑在 config.yaml 增大 "
                f"{self._timeout_setting_path(field)}。"
            )
        else:
            detail = (
                f"请求超时（已耗时 {elapsed:.1f}s，上限 {budget}s，"
                f"provider {self._provider!r}），请稍后重试或在 config.yaml 增大 "
                f"{self._timeout_setting_path(field)}。"
            )
        # A submit whose response was lost is the only case that can have left
        # billed work behind under a task_id nobody will ever see.
        indeterminate = not connect_phase and method.upper() == "POST"
        if indeterminate:
            detail += (
                "（请求已经发出但没等到回应，上游可能已经受理并开始计费；"
                "本服务没有拿到 task_id，也没有落库，无法确认。"
                "重发有重复计费的风险，请先到上游侧确认。）"
            )
        return CFGPUError(
            error_type="timeout",
            user_message=detail,
            original={
                "url": url,
                "phase": "connect" if connect_phase else "request",
                "method": method.upper(),
                "elapsed": round(elapsed, 1),
                "timeout": budget,
                "provider": self._provider,
            },
            retryable=not indeterminate,
            outcome_unknown=indeterminate,
        )

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def post(self, path: str, json: dict) -> dict:
        return await self._request("POST", path, json=json)

    async def get(self, path: str, *, raise_on_body_error: bool = True) -> dict:
        """GET, optionally letting an HTTP-200 body error through to the caller.

        ``raise_on_body_error=False`` is what ``TaskManager.poll`` passes. On a poll
        the transport succeeded — HTTP 200, the upstream answered — and the body *is*
        the task record, so its ``error`` field is the task's verdict, not this call's
        failure. Raising it here made a dead task indistinguishable from an unreachable
        one: ``wait()`` absorbed it as a transient poll failure and handed back
        ``status: "running"``, and ``get_status``'s re-poll swallowed it as transient
        and returned the stale running row — forever. Deciding that belongs to the layer
        that knows what a task record looks like.
        """
        return await self._request("GET", path, raise_on_body_error=raise_on_body_error)

    async def _request(
        self, method: str, path: str, *, raise_on_body_error: bool = True, **kwargs
    ) -> dict:
        url = f"{self._base_url}/{path.lstrip('/')}"
        if method == "POST" and os.getenv("CFGPU_DRY_RUN"):
            logger.info("DRY-RUN POST %s\n%s", url, _json.dumps(kwargs.get("json", {}), ensure_ascii=False, indent=2))
        token = self._resolve_token()
        headers = {
            "Authorization": token if self._auth_scheme == "raw" else f"Bearer {token}",
            "Accept-Encoding": ACCEPT_ENCODING,
        }
        session = await self._get_session()
        started = time.monotonic()
        try:
            async with session.request(method, url, headers=headers, **kwargs) as resp:
                body: dict = {}
                try:
                    body = await resp.json(content_type=None)
                except Exception:
                    body = {"message": await resp.text()}

                if not isinstance(body, dict):
                    body = {"_raw": body}

                body_error = isinstance(body.get("error"), dict) and bool(body["error"])
                if not resp.ok or (body_error and raise_on_body_error):
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
            raise self._timeout_error(
                e, url, elapsed=time.monotonic() - started, method=method
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
