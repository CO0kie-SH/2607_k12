from __future__ import annotations

import base64
import hashlib
import json
import logging
import asyncio
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def token_preview(token: str) -> str:
    if len(token) <= 18:
        return token
    return f"{token[:10]}...{token[-8:]}"


def decode_access_token(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError("AccessToken is not a JWT")
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    auth = claims.get("https://api.openai.com/auth", {})
    profile = claims.get("https://api.openai.com/profile", {})
    return {
        "email": profile.get("email", ""),
        "phone": profile.get("phone_number", ""),
        "plan_type": auth.get("chatgpt_plan_type", ""),
        "account_id": auth.get("chatgpt_account_id", ""),
        "account_user_id": auth.get("chatgpt_account_user_id", ""),
        "user_id": auth.get("chatgpt_user_id") or auth.get("user_id", ""),
        "client_id": claims.get("client_id", ""),
        "issuer": claims.get("iss", ""),
        "issued_at": claims.get("iat"),
        "expires_at": claims.get("exp"),
        "scopes": claims.get("scp", []),
        "raw_claims": claims,
    }


class K12Service:
    def __init__(
        self,
        db_dir: Path,
        *,
        log_dir: Path | None = None,
        base_url: str = "https://chatgpt.com",
        proxy: str | None = None,
        timeout: float = 15.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self.db_dir = db_dir
        self.log_dir = log_dir or db_dir
        self.base_url = base_url.rstrip("/")
        self.proxy = proxy or None
        self.timeout = timeout
        self.log = logger or logging.getLogger(__name__)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _client_session(self, timeout: aiohttp.ClientTimeout) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(timeout=timeout, proxy=self.proxy, trust_env=False)

    @staticmethod
    def _is_js_challenge(text: str, content_type: str, status: int) -> bool:
        if status != 403 or "text/html" not in content_type.lower():
            return False
        return "Enable JavaScript and cookies to continue" in text

    @staticmethod
    def _curl_quote(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    async def inspect_access_token(
        self,
        access_token: str,
        operator_log: str = "",
        progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        token = access_token.strip()
        if not token.startswith("eyJ"):
            raise ValueError("AT 必须以 eyJ 开头")

        await self._progress(progress, "start", "收到 AT，开始处理")
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        await self._progress(progress, "decode", "解析 AT 中")
        decoded = decode_access_token(token)
        await self._progress(
            progress,
            "decode_done",
            "得到 AT 账号信息",
            {
                "account_id": decoded.get("account_id", ""),
                "email": decoded.get("email", ""),
                "phone": decoded.get("phone", ""),
                "plan_type": decoded.get("plan_type", ""),
            },
        )
        api_data = await self._query_account(token, progress=progress)

        report = {
            "generated_at": utc_now(),
            "access_token_sha256": token_hash,
            "access_token_preview": token_preview(token),
            "operator_log": operator_log,
            "decoded": decoded,
            "query": api_data,
        }
        report["workspace_ids"] = self._extract_workspace_ids(api_data.get("accounts"))
        report["workspace_details"] = self._extract_workspace_details(api_data.get("accounts"))
        report["workspace_count"] = len(report["workspace_ids"])

        account_id = decoded.get("account_id") or token_hash[:16]
        report_path = self.db_dir / f"k12_{account_id}.json"
        report["report_path"] = str(report_path)
        await self._progress(progress, "export", f"导出查询 JSON 中: {report_path}")
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        await self._progress(progress, "export_done", f"查询 JSON 已导出: {report_path}")

        record = {
            "recorded_at": utc_now(),
            "access_token_sha256": token_hash,
            "access_token_preview": token_preview(token),
            "account_id": decoded.get("account_id", ""),
            "email": decoded.get("email", ""),
            "phone": decoded.get("phone", ""),
            "plan_type": decoded.get("plan_type", ""),
            "report_path": str(report_path),
        }
        with (self.db_dir / "k12_at_records.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

        if operator_log.strip():
            await self._progress(progress, "log", "保存操作日志中")
            self.save_operator_log(operator_log)
            await self._progress(progress, "log_done", "操作日志已保存")

        self.log.info("k12 report exported account_id=%s path=%s", decoded.get("account_id"), report_path)
        await self._progress(progress, "done", "查询流程完成")
        return {
            "report": report,
            "report_path": str(report_path),
            "account_info": self._account_info(report),
        }

    async def apply_workspaces(
        self,
        access_token: str,
        workspace_ids: list[str],
        operator_log: str = "",
        progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        token = access_token.strip()
        if not token.startswith("eyJ"):
            raise ValueError("AT 必须以 eyJ 开头")

        ids = [workspace_id.strip() for workspace_id in workspace_ids if workspace_id.strip()]
        if not ids:
            raise ValueError("workspace_ids 不能为空")

        results: list[dict[str, Any]] = []
        accepted_workspace_id = ""
        progress_log: list[str] = []

        async def apply_progress(payload: dict[str, Any]) -> None:
            message = payload.get("message") or payload.get("stage") or ""
            if message:
                progress_log.append(f"[{payload.get('time') or utc_now()}]{message}")
            if progress is not None:
                await progress(payload)

        await self._progress(apply_progress, "apply_start", f"开始申请空间，共{len(ids)}个")

        for index, workspace_id in enumerate(ids, 1):
            short_id = self._short_workspace_id(workspace_id)
            await self._progress(
                apply_progress,
                "apply_request",
                f"申请{short_id} request中",
                {"workspace_id": workspace_id, "index": index, "total": len(ids)},
            )
            request_result = await self._post_empty(
                f"{self.base_url}/backend-api/accounts/{workspace_id}/invites/request",
                token,
            )
            row = {
                "workspace_id": workspace_id,
                "request_ok": bool(request_result.get("ok")),
                "request": request_result,
                "accept_ok": False,
                "accept": None,
                "status": "request_failed",
            }
            if not row["request_ok"]:
                await self._progress(
                    apply_progress,
                    "apply_request_failed",
                    f"申请{short_id} request失败",
                    {"workspace_id": workspace_id, "result": request_result},
                )
                results.append(row)
                continue

            await self._progress(
                apply_progress,
                "apply_request_done",
                f"申请{short_id} request成功",
                {"workspace_id": workspace_id, "result": request_result},
            )
            await asyncio.sleep(1.5)

            await self._progress(
                apply_progress,
                "apply_accept",
                f"申请{short_id} accept中",
                {"workspace_id": workspace_id, "index": index, "total": len(ids)},
            )
            accept_result = await self._post_empty(
                f"{self.base_url}/backend-api/accounts/{workspace_id}/invites/accept",
                token,
            )
            row["accept"] = accept_result
            row["accept_ok"] = bool(accept_result.get("ok"))
            row["status"] = "accepted" if row["accept_ok"] else "accept_failed"
            results.append(row)

            if row["accept_ok"]:
                accepted_workspace_id = workspace_id
                await self._progress(
                    apply_progress,
                    "apply_accept_done",
                    f"申请{short_id}成功，停止后续申请",
                    {"workspace_id": workspace_id, "result": accept_result},
                )
                await asyncio.sleep(2.0)
                break

            await self._progress(
                apply_progress,
                "apply_accept_failed",
                f"申请{short_id} accept失败",
                {"workspace_id": workspace_id, "result": accept_result},
            )

        success = bool(accepted_workspace_id)
        stopped_by = "first_success" if success else "exhausted"
        await self._progress(apply_progress, "apply_refresh", "刷新账号信息中")
        account_report = await self.inspect_access_token(token, "", progress=apply_progress)

        await self._progress(
            apply_progress,
            "apply_done",
            "申请空间流程完成",
            {"success": success, "accepted_workspace_id": accepted_workspace_id, "stopped_by": stopped_by},
        )
        final_operator_log = "\n".join(item for item in [operator_log.strip(), "\n".join(progress_log)] if item)
        if final_operator_log:
            self.save_operator_log(final_operator_log)
        return {
            "success": success,
            "stopped_by": stopped_by,
            "accepted_workspace_id": accepted_workspace_id,
            "results": results,
            "account_report": account_report,
        }

    def save_operator_log(self, text: str) -> dict[str, Any]:
        payload = text.strip()
        if not payload:
            raise ValueError("日志内容不能为空")
        path = self.log_dir / "k12_operator.log"
        entry = f"[{utc_now()}]\n{payload}\n\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(entry)
        return {"path": str(path), "bytes": len(entry.encode("utf-8")), "saved_at": utc_now()}

    def latest_report(self) -> dict[str, Any] | None:
        reports = sorted(self.db_dir.glob("k12_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not reports:
            return None
        data = json.loads(reports[0].read_text(encoding="utf-8"))
        return {"report": data, "report_path": str(reports[0]), "account_info": self._account_info(data)}

    async def _query_account(
        self,
        access_token: str,
        progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "accept": "*/*",
            "authorization": f"Bearer {access_token}",
            "content-type": "application/json",
            "oai-device-id": str(uuid.uuid4()),
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/135.0.0.0 Safari/537.36",
        }
        result: dict[str, Any] = {}
        endpoints = [
            ("me", "/backend-api/me", "查询账号基础信息中"),
            ("accounts", "/backend-api/accounts", "查询账号空间信息中"),
        ]
        for name, path, message in endpoints:
            url = f"{self.base_url}{path}"
            await self._progress(progress, f"query_{name}", f"{message}: {path}", {"url": url})
            endpoint_result = await self._request_json(url, headers)
            result[name] = endpoint_result
            status = endpoint_result.get("status")
            content_type = endpoint_result.get("content_type", "")
            if endpoint_result.get("ok"):
                await self._progress(
                    progress,
                    f"query_{name}_done",
                    f"得到接口信息: {path}",
                    {"status": status, "content_type": content_type},
                )
            elif endpoint_result.get("error"):
                await self._progress(
                    progress,
                    f"query_{name}_error",
                    f"接口查询失败: {path} {endpoint_result['error']}",
                    {"error": endpoint_result["error"]},
                )
            elif endpoint_result.get("json_error"):
                await self._progress(
                    progress,
                    f"query_{name}_non_json",
                    f"接口返回非 JSON: {path} status={status}",
                    {
                        "status": status,
                        "content_type": content_type,
                        "text_preview": endpoint_result.get("text_preview", "")[:160],
                    },
                )
            else:
                await self._progress(
                    progress,
                    f"query_{name}_done",
                    f"接口返回非 200: {path} status={status}",
                    {
                        "status": status,
                        "content_type": content_type,
                        "text_preview": endpoint_result.get("text_preview", "")[:160],
                    },
                )
        return result

    async def _request_json(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        result = await self._aiohttp_request(
            "GET",
            url,
            headers,
            timeout,
            parse_json=True,
            preview_limit=500,
        )
        if result.get("js_challenge"):
            return await self._curl_request(
                "GET",
                url,
                headers,
                timeout_seconds=self.timeout,
                parse_json=True,
                preview_limit=500,
                fallback_from=result,
            )
        return result

    async def _aiohttp_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        timeout: aiohttp.ClientTimeout,
        *,
        parse_json: bool,
        preview_limit: int,
        data: bytes | None = None,
    ) -> dict[str, Any]:
        try:
            self.log.info("k12 http %s url=%s proxy=%s", method.lower(), url, self.proxy or "-")
            async with self._client_session(timeout) as session:
                async with session.request(method, url, headers=headers, data=data) as resp:
                    text = await resp.text()
                    content_type = resp.headers.get("content-type", "")
                    parsed: Any = None
                    json_error = ""
                    if parse_json and text:
                        try:
                            parsed = json.loads(text)
                        except ValueError as exc:
                            json_error = repr(exc)
                    return {
                        "status": resp.status,
                        "ok": resp.status == 200 and (not parse_json or not json_error),
                        "content_type": content_type,
                        "text_length": len(text),
                        "text_preview": text[:preview_limit],
                        "json_error": json_error,
                        "data": parsed,
                        "proxy_used": self.proxy or "",
                        "transport": "aiohttp",
                        "js_challenge": self._is_js_challenge(text, content_type, resp.status),
                    }
        except Exception as exc:
            return {"ok": False, "error": repr(exc), "proxy_used": self.proxy or "", "transport": "aiohttp"}

    async def _curl_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        *,
        timeout_seconds: float,
        parse_json: bool,
        preview_limit: int,
        fallback_from: dict[str, Any] | None = None,
        data: bytes | None = None,
    ) -> dict[str, Any]:
        curl = shutil.which("curl.exe") or shutil.which("curl")
        if not curl:
            return {
                "ok": False,
                "error": "curl executable not found for JS challenge fallback",
                "proxy_used": self.proxy or "",
                "transport": "curl",
            }

        marker = "__K12_CURL_META__"
        cmd = [
            curl,
            "-sS",
            "--http1.1",
            "--compressed",
            "--location",
            "--max-time",
            str(max(1, int(timeout_seconds))),
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
            cmd.extend(["--request", method.upper(), "--data-raw", (data or b"").decode("utf-8", errors="ignore")])

        config_lines = [f"url = {self._curl_quote(url)}"]
        config_lines.extend(f"header = {self._curl_quote(f'{key}: {value}')}" for key, value in headers.items())
        config = ("\n".join(config_lines) + "\n").encode("utf-8")
        self.log.info("k12 http curl fallback method=%s url=%s proxy=%s", method.lower(), url, self.proxy or "-")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(config), timeout=timeout_seconds + 5)
        except Exception as exc:
            return {
                "ok": False,
                "error": repr(exc),
                "proxy_used": self.proxy or "",
                "transport": "curl",
            }

        output = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if marker not in output:
            return {
                "ok": False,
                "error": stderr_text or "curl response metadata missing",
                "proxy_used": self.proxy or "",
                "transport": "curl",
                "text_preview": output[:preview_limit],
            }

        text, meta = output.rsplit(f"\n{marker}", 1)
        status_text, _, content_type = meta.partition("|")
        try:
            status = int(status_text)
        except ValueError:
            status = 0

        parsed: Any = None
        json_error = ""
        if parse_json and text:
            try:
                parsed = json.loads(text)
            except ValueError as exc:
                json_error = repr(exc)

        result = {
            "status": status,
            "ok": status == 200 and (not parse_json or not json_error),
            "content_type": content_type.strip(),
            "text_length": len(text),
            "text_preview": text[:preview_limit],
            "json_error": json_error,
            "data": parsed,
            "proxy_used": self.proxy or "",
            "transport": "curl",
            "curl_stderr": stderr_text,
        }
        if fallback_from is not None:
            result["fallback_from"] = {
                "transport": fallback_from.get("transport"),
                "status": fallback_from.get("status"),
                "content_type": fallback_from.get("content_type"),
                "js_challenge": fallback_from.get("js_challenge"),
            }
        return result

    async def _post_empty(self, url: str, access_token: str) -> dict[str, Any]:
        headers = {
            "accept": "*/*",
            "authorization": f"Bearer {access_token}",
            "content-type": "application/json",
            "oai-device-id": str(uuid.uuid4()),
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/135.0.0.0 Safari/537.36",
        }
        timeout = aiohttp.ClientTimeout(total=3)
        result = await self._aiohttp_request(
            "POST",
            url,
            headers,
            timeout,
            parse_json=False,
            preview_limit=300,
            data=b"",
        )
        if result.get("js_challenge"):
            return await self._curl_request(
                "POST",
                url,
                headers,
                timeout_seconds=3,
                parse_json=False,
                preview_limit=300,
                fallback_from=result,
                data=b"",
            )
        return result

    @staticmethod
    def _short_workspace_id(workspace_id: str) -> str:
        if len(workspace_id) <= 12:
            return workspace_id
        return f"{workspace_id[:4]}...{workspace_id[-3:]}"

    @staticmethod
    async def _progress(
        progress: Callable[[dict[str, Any]], Awaitable[None]] | None,
        stage: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        if progress is None:
            return
        await progress({"stage": stage, "message": message, "data": data or {}, "time": utc_now()})

    @staticmethod
    def _extract_workspace_ids(accounts_query: Any) -> list[str]:
        ids: set[str] = set()
        data = accounts_query.get("data") if isinstance(accounts_query, dict) else accounts_query

        def walk(obj: Any, depth: int = 0) -> None:
            if depth > 6:
                return
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if key == "id" and isinstance(value, str) and len(value) == 36 and value.count("-") == 4:
                        ids.add(value)
                    walk(value, depth + 1)
            elif isinstance(obj, list):
                for item in obj:
                    walk(item, depth + 1)

        walk(data)
        return sorted(ids)

    @staticmethod
    def _extract_workspace_details(accounts_query: Any) -> list[dict[str, str]]:
        data = accounts_query.get("data") if isinstance(accounts_query, dict) else accounts_query
        items = data.get("items", []) if isinstance(data, dict) else []
        details: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            workspace_id = item.get("id")
            if not isinstance(workspace_id, str) or not workspace_id:
                continue
            details.append(
                {
                    "id": workspace_id,
                    "type": str(item.get("structure") or item.get("type") or "-"),
                    "name": str(item.get("name") or "-"),
                    "role": str(item.get("current_user_role") or "-"),
                }
            )
        return details

    @staticmethod
    def _account_info(report: dict[str, Any]) -> str:
        decoded = report.get("decoded", {})
        query = report.get("query", {})
        me = query.get("me", {})
        accounts = query.get("accounts", {})

        def endpoint_line(path: str, item: dict[str, Any]) -> str:
            if item.get("error"):
                return f"{path}: {item.get('error')}"
            status = item.get("status", "-")
            content_type = item.get("content_type") or "-"
            json_error = item.get("json_error") or ""
            suffix = f" status={status} content-type={content_type}"
            if item.get("proxy_used"):
                suffix += f" proxy={item.get('proxy_used')}"
            if json_error:
                suffix += f" json_error={json_error}"
            preview = item.get("text_preview") or ""
            if preview:
                suffix += f"\n  preview: {preview[:180]}"
            return f"{path}:{suffix}"

        lines = [
            f"生成时间: {report.get('generated_at', '')}",
            f"报告路径: {report.get('report_path', '')}",
            f"邮箱: {decoded.get('email') or '-'}",
            f"手机: {decoded.get('phone') or '-'}",
            f"计划: {decoded.get('plan_type') or '-'}",
            f"账号 ID: {decoded.get('account_id') or '-'}",
            f"用户 ID: {decoded.get('user_id') or '-'}",
            f"Workspace 数量: {report.get('workspace_count', 0)}",
            f"Workspace IDs: {', '.join(report.get('workspace_ids', [])) or '-'}",
            "Workspace 详情:",
            *[
                f"  {item.get('id', '-')}: 类型={item.get('type', '-')} 名称={item.get('name', '-')}"
                for item in report.get("workspace_details", [])
            ],
            endpoint_line("/backend-api/me", me),
            endpoint_line("/backend-api/accounts", accounts),
        ]
        return "\n".join(lines)
