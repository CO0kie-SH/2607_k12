from __future__ import annotations

from typing import Any

from curl_cffi.requests import AsyncSession

from .base_http_client import BaseHttpClient


class CurlCffiHttpClient(BaseHttpClient):
    transport = "curl_cffi"

    def __init__(self, *, impersonate: str = "chrome", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.impersonate = impersonate

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
            session_kwargs = {"timeout": timeout or self.timeout, "impersonate": self.impersonate}
            if self.proxy:
                session_kwargs["proxy"] = self.proxy
            async with AsyncSession(**session_kwargs) as session:
                resp = await session.request(method.upper(), url, headers=headers, data=data)
            return self._result(
                status=resp.status_code,
                content_type=resp.headers.get("content-type", ""),
                text=resp.text,
                parse_json=parse_json,
                preview_limit=preview_limit,
                extra={"impersonate": self.impersonate},
            )
        except Exception as exc:
            return self._error(exc)
