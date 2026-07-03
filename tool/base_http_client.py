from __future__ import annotations

import json
import logging
from typing import Any


class BaseHttpClient:
    transport = "base"

    def __init__(
        self,
        *,
        proxy: str | None = None,
        timeout: float = 15.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self.proxy = proxy or None
        self.timeout = timeout
        self.log = logger or logging.getLogger(__name__)

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
        raise NotImplementedError

    def _result(
        self,
        *,
        status: int,
        content_type: str,
        text: str,
        parse_json: bool,
        preview_limit: int,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        parsed: Any = None
        json_error = ""
        if parse_json and text:
            try:
                parsed = json.loads(text)
            except ValueError as exc:
                json_error = repr(exc)

        result = {
            "status": status,
            "ok": 200 <= status < 300 and (not parse_json or not json_error),
            "content_type": content_type,
            "text_length": len(text),
            "text_preview": text[:preview_limit],
            "json_error": json_error,
            "data": parsed,
            "proxy_used": self.proxy or "",
            "transport": self.transport,
        }
        if extra:
            result.update(extra)
        return result

    def _error(self, exc: Exception | str) -> dict[str, Any]:
        return {
            "ok": False,
            "error": repr(exc) if isinstance(exc, Exception) else exc,
            "proxy_used": self.proxy or "",
            "transport": self.transport,
        }
