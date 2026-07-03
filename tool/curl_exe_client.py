from __future__ import annotations

import asyncio
import shutil
from typing import Any

from .base_http_client import BaseHttpClient


class CurlExeHttpClient(BaseHttpClient):
    transport = "curl.exe"

    @staticmethod
    def _quote(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

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
        curl = shutil.which("curl.exe") or shutil.which("curl")
        if not curl:
            return self._error("curl executable not found")

        marker = "__K12_CURL_META__"
        timeout_seconds = max(1, int(timeout or self.timeout))
        cmd = [
            curl,
            "-sS",
            "--http1.1",
            "--compressed",
            "--location",
            "--max-time",
            str(timeout_seconds),
            "--output",
            "-",
            "--write-out",
            f"\n{marker}%{{http_code}}|%{{content_type}}",
            "--config",
            "-",
        ]
        if self.proxy:
            cmd.extend(["--proxy", self.proxy])
        if method.upper() != "GET":
            body = data.decode("utf-8", errors="ignore") if isinstance(data, bytes) else data or ""
            cmd.extend(["--request", method.upper(), "--data-raw", body])

        config_lines = [f"url = {self._quote(url)}"]
        config_lines.extend(f"header = {self._quote(f'{key}: {value}')}" for key, value in headers.items())
        config = ("\n".join(config_lines) + "\n").encode("utf-8")

        try:
            self.log.info("k12 http %s url=%s proxy=%s transport=%s", method.lower(), url, self.proxy or "-", self.transport)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(config), timeout=timeout_seconds + 5)
        except Exception as exc:
            return self._error(exc)

        output = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if marker not in output:
            result = self._error(stderr_text or "curl response metadata missing")
            result["text_preview"] = output[:preview_limit]
            return result

        text, meta = output.rsplit(f"\n{marker}", 1)
        status_text, _, content_type = meta.partition("|")
        try:
            status = int(status_text)
        except ValueError:
            status = 0
        return self._result(
            status=status,
            content_type=content_type.strip(),
            text=text,
            parse_json=parse_json,
            preview_limit=preview_limit,
            extra={"curl_stderr": stderr_text},
        )
