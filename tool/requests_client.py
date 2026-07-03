from __future__ import annotations

import asyncio
from typing import Any

from .base_http_client import BaseHttpClient


class RequestsHttpClient(BaseHttpClient):
    transport = "requests"

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
            import requests
        except ImportError as exc:
            return self._error(f"requests is not installed: {exc}")

        def send() -> Any:
            proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
            return requests.request(method.upper(), url, headers=headers, data=data, proxies=proxies, timeout=timeout or self.timeout)

        try:
            self.log.info("k12 http %s url=%s proxy=%s transport=%s", method.lower(), url, self.proxy or "-", self.transport)
            resp = await asyncio.to_thread(send)
            return self._result(
                status=resp.status_code,
                content_type=resp.headers.get("content-type", ""),
                text=resp.text,
                parse_json=parse_json,
                preview_limit=preview_limit,
            )
        except Exception as exc:
            return self._error(exc)
