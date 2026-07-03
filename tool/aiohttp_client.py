from __future__ import annotations

from typing import Any

import aiohttp

from .base_http_client import BaseHttpClient


class AiohttpHttpClient(BaseHttpClient):
    transport = "aiohttp"

    async def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        *,
        timeout: float | None = None,
        parse_json: bool = True,
        preview_limit: int = 500,
        data: bytes | str | None = None,
    ) -> dict[str, Any]:
        try:
            self.log.info("k12 http %s url=%s proxy=%s transport=%s", method.lower(), url, self.proxy or "-", self.transport)
            client_timeout = aiohttp.ClientTimeout(total=timeout or self.timeout)
            async with aiohttp.ClientSession(timeout=client_timeout, proxy=self.proxy, trust_env=False) as session:
                async with session.request(method.upper(), url, headers=headers, data=data) as resp:
                    text = await resp.text()
                    return self._result(
                        status=resp.status,
                        content_type=resp.headers.get("content-type", ""),
                        text=text,
                        parse_json=parse_json,
                        preview_limit=preview_limit,
                    )
        except Exception as exc:
            return self._error(exc)
