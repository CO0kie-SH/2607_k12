from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
from contextlib import suppress
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web

from . import jsonrpc
from .auth_service import (
    LOCAL_WHITELIST_USABLE_COUNT,
    LOCAL_WHITELIST_USERNAME,
    NAMED_WHITELIST_USERNAMES,
    SESSION_COOKIE,
    AuthService,
    request_headers_snapshot,
)
from .k12_service import K12Service

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "static"
DEFAULT_DB_DIR = ROOT / "db"
DEFAULT_LOG_DIR = ROOT / "log"
DEFAULT_AUTH_DB = DEFAULT_DB_DIR / "auth.sqlite3"
DEFAULT_WORKSPACE_CSV = ROOT / "k12.csv"
DEFAULT_K12_PROXY = "http://127.0.0.1:7897"
DIRECT_K12_PROXY_VALUES = {"", "none", "no", "false", "0", "direct", "off", "null"}
DEFAULT_ALLOWED_ORIGINS = (
    "http://127.0.0.1:8088",
    "http://localhost:8088",
    "http://[::1]:8088",
)
LOCAL_WHITELIST_REMOTE = "127.0.0.1"
WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\r\n\"']+")
WHITELIST_WS_CLIENT_TIMEOUT_SECONDS = 600
LOCAL_WHITELIST_WS_CLIENT_TIMEOUT_SECONDS = 1800
WHITELIST_WS_SERVER_GRACE_SECONDS = 10
WHITELIST_WS_TIMEOUT_CHECK_INTERVAL_SECONDS = 1.0


def setup_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("k12_dashboard")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    file_handler = RotatingFileHandler(log_dir / "k12_server.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    access_logger = logging.getLogger("aiohttp.access")
    access_logger.handlers.clear()
    access_logger.addHandler(file_handler)
    access_logger.setLevel(logging.INFO)
    return logger


def client_safe_payload(value: Any) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            if key == "report_path":
                continue
            if key == "path" and isinstance(item, str) and WINDOWS_PATH_RE.search(item):
                continue
            safe[key] = client_safe_payload(item)
        return safe
    if isinstance(value, list):
        return [client_safe_payload(item) for item in value]
    if isinstance(value, str):
        return WINDOWS_PATH_RE.sub("[server-path]", value)
    return value


async def send_json(ws: web.WebSocketResponse, payload: dict[str, Any]) -> None:
    await ws.send_str(json.dumps(client_safe_payload(payload), ensure_ascii=False))


def auth_user(request: web.Request):
    return request.app["auth"].user_from_token(request.cookies.get(SESSION_COOKIE, ""))


def normalize_origin(origin: str) -> str:
    origin = origin.strip().rstrip("/")
    if not origin:
        return ""
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def host_name_from_header(host: str) -> str:
    host = host.strip()
    if not host:
        return ""
    return (urlsplit(f"//{host}").hostname or "").lower()


def parse_allowed_origins(value: str | None) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_ALLOWED_ORIGINS
    origins: list[str] = []
    seen: set[str] = set()
    for item in value.split(","):
        origin = normalize_origin(item)
        if origin and origin not in seen:
            origins.append(origin)
            seen.add(origin)
    return tuple(origins)


def normalize_k12_proxy(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in DIRECT_K12_PROXY_VALUES:
        return None
    return text


def is_allowed_ws_origin(request: web.Request) -> bool:
    origin = normalize_origin(request.headers.get("Origin", ""))
    if not origin:
        return False
    return origin in request.app["allowed_origins"]


def no_store(response: web.StreamResponse) -> web.StreamResponse:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def is_local_whitelist_remote(remote: str) -> bool:
    return remote == LOCAL_WHITELIST_REMOTE


def named_whitelist_username(username: str) -> str:
    normalized = username.strip().lower()
    for item in NAMED_WHITELIST_USERNAMES:
        if normalized == item.lower():
            return item
    return ""


def is_whitelist_account(username: str) -> bool:
    username = username.strip()
    return username == LOCAL_WHITELIST_USERNAME or bool(named_whitelist_username(username))


def whitelist_ws_timeout_config(request: web.Request) -> dict[str, Any]:
    headers_snapshot = request_headers_snapshot(request)
    host_name = host_name_from_header(request.headers.get("Host", ""))
    effective_remote = str(headers_snapshot.get("remote") or "")
    socket_remote = str(headers_snapshot.get("socket_remote") or request.remote or "")
    local_timeout = host_name == LOCAL_WHITELIST_REMOTE or effective_remote == LOCAL_WHITELIST_REMOTE or socket_remote == LOCAL_WHITELIST_REMOTE
    client_timeout_seconds = LOCAL_WHITELIST_WS_CLIENT_TIMEOUT_SECONDS if local_timeout else WHITELIST_WS_CLIENT_TIMEOUT_SECONDS
    return {
        "profile": "local_127" if local_timeout else "default",
        "client_timeout_seconds": client_timeout_seconds,
        "server_timeout_seconds": client_timeout_seconds + WHITELIST_WS_SERVER_GRACE_SECONDS,
    }


def websocket_session_count(app: web.Application, username: str) -> int:
    username = username.strip()
    if not username:
        return 0
    client_users: dict[web.WebSocketResponse, str] = app["client_users"]
    dead: list[web.WebSocketResponse] = []
    total = 0
    for ws, ws_username in list(client_users.items()):
        if ws.closed:
            dead.append(ws)
            continue
        if ws_username == username:
            total += 1
    for ws in dead:
        client_users.pop(ws, None)
        app["clients"].discard(ws)
    return total


def auth_session_counts(app: web.Application, username: str) -> dict[str, int]:
    return {
        "active_session_count": app["auth"].active_session_count(username),
        "websocket_session_count": websocket_session_count(app, username),
    }


def is_workspace_csv_header(line: str) -> bool:
    first_column = line.split(",", 1)[0].strip().lower().lstrip("\ufeff")
    return first_column == "workspace_id"


def load_workspace_csv_content(path: Path, logger: logging.Logger) -> str:
    if not path.exists():
        logger.warning("workspace csv not found path=%s", path)
        return ""
    raw_content = path.read_text(encoding="utf-8-sig")
    lines = [line.strip() for line in raw_content.splitlines() if line.strip()]
    content_lines = [line for line in lines if not is_workspace_csv_header(line)]
    content = "\n".join(content_lines)
    line_count = len(content_lines)
    logger.info("workspace csv cached path=%s lines=%s bytes=%s", path, line_count, len(content.encode("utf-8")))
    return content


async def index(_: web.Request) -> web.FileResponse:
    return no_store(web.FileResponse(STATIC_DIR / "index.html"))


async def websocket_page(request: web.Request) -> web.FileResponse:
    token = request.cookies.get(SESSION_COOKIE, "")
    entry_token = request.query.get("entry", "")
    user = request.app["auth"].consume_entry_token(token, entry_token)
    if user is None:
        request.app["logger"].warning("blocked websocket page session remote=%s path=%s", request.remote, request.path_qs)
        response = web.HTTPFound("/?session=expired")
        response.del_cookie(SESSION_COOKIE, path="/")
        raise response
    request.app["logger"].info("websocket page entry consumed user=%s remote=%s", user.username, request.remote)
    return no_store(web.FileResponse(STATIC_DIR / "websocket.html"))


async def js_page(_: web.Request) -> web.FileResponse:
    return no_store(web.FileResponse(STATIC_DIR / "js.html"))


async def api_status(request: web.Request) -> web.Response:
    user = auth_user(request)
    if user is None:
        return web.json_response({"authenticated": False, "message": "login required"}, status=401)
    return web.json_response(
        client_safe_payload(
            {
                "event_id": jsonrpc.make_event_id("http_status"),
                "auth": {"username": user.username, "usable_count": user.usable_count},
                "k12_proxy": request.app["k12_proxy"],
                "clients": len(request.app["clients"]),
            }
        )
    )


async def read_json_body(request: web.Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except json.JSONDecodeError:
        raise web.HTTPBadRequest(text="Invalid JSON")
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="JSON body must be an object")
    return data


async def api_auth_query(request: web.Request) -> web.Response:
    data = await read_json_body(request)
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    headers_snapshot = request_headers_snapshot(request)
    risk = request.app["auth"].record_request(headers_snapshot)
    named_whitelist = named_whitelist_username(username)
    named_whitelisted = bool(named_whitelist)
    local_whitelisted = (not named_whitelisted) and is_local_whitelist_remote(risk.remote)
    user = None if (named_whitelisted or local_whitelisted) else request.app["auth"].query_user(username, password)
    whitelist_username = named_whitelist if named_whitelisted else LOCAL_WHITELIST_USERNAME
    request.app["logger"].info(
        "auth query username=%s ok=%s local_whitelist=%s named_whitelist=%s remote=%s remote_source=%s request_count=%s window_seconds=%s environment=%s headers=%s",
        whitelist_username if (named_whitelisted or local_whitelisted) else username or "-",
        named_whitelisted or local_whitelisted or bool(user),
        local_whitelisted,
        named_whitelisted,
        risk.remote,
        headers_snapshot.get("remote_source") or "-",
        risk.request_count,
        risk.window_seconds,
        risk.environment_key,
        json.dumps(headers_snapshot, ensure_ascii=False, separators=(",", ":")),
    )
    if named_whitelisted or local_whitelisted:
        counts = auth_session_counts(request.app, whitelist_username)
        return web.json_response(
            {
                "ok": True,
                "username": whitelist_username,
                "usable_count": LOCAL_WHITELIST_USABLE_COUNT,
                "is_active": True,
                "remote": risk.remote,
                "request_count": risk.request_count,
                "window_seconds": risk.window_seconds,
                "session_mark": whitelist_username,
                "local_whitelist": local_whitelisted,
                "named_whitelist": named_whitelisted,
                **counts,
            }
        )
    if user is None:
        return web.json_response(
            {
                "ok": False,
                "message": "账号或密码错误",
                "remote": risk.remote,
                "request_count": risk.request_count,
                "window_seconds": risk.window_seconds,
            },
            status=401,
        )
    counts = auth_session_counts(request.app, user.username)
    return web.json_response(
        {
            "ok": True,
            "username": user.username,
            "usable_count": user.usable_count,
            "is_active": user.is_active,
            "remote": risk.remote,
            "request_count": risk.request_count,
            "window_seconds": risk.window_seconds,
            **counts,
        }
    )


async def api_auth_login(request: web.Request) -> web.Response:
    data = await read_json_body(request)
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    headers_snapshot = request_headers_snapshot(request)
    risk = request.app["auth"].record_request(headers_snapshot)
    named_whitelist = named_whitelist_username(username)
    named_whitelisted = bool(named_whitelist)
    local_whitelisted = (not named_whitelisted) and is_local_whitelist_remote(risk.remote)
    whitelist_username = named_whitelist if named_whitelisted else LOCAL_WHITELIST_USERNAME
    if named_whitelisted:
        login_result = request.app["auth"].trusted_named_login(named_whitelist, headers_snapshot)
    elif local_whitelisted:
        login_result = request.app["auth"].trusted_local_login(headers_snapshot)
    else:
        login_result = request.app["auth"].login(username, password, headers_snapshot)
    request.app["logger"].info(
        "auth login username=%s ok=%s local_whitelist=%s named_whitelist=%s remote=%s remote_source=%s request_count=%s window_seconds=%s environment=%s headers=%s",
        whitelist_username if (named_whitelisted or local_whitelisted) else username or "-",
        bool(login_result),
        local_whitelisted,
        named_whitelisted,
        risk.remote,
        headers_snapshot.get("remote_source") or "-",
        risk.request_count,
        risk.window_seconds,
        risk.environment_key,
        json.dumps(headers_snapshot, ensure_ascii=False, separators=(",", ":")),
    )
    if login_result is None:
        return web.json_response(
            {
                "ok": False,
                "message": "账号或密码错误，或可用次数不足",
                "remote": risk.remote,
                "request_count": risk.request_count,
                "window_seconds": risk.window_seconds,
            },
            status=401,
        )
    token, entry_token, user = login_result
    counts = auth_session_counts(request.app, user.username)
    response = web.json_response(
        {
            "ok": True,
            "username": user.username,
            "usable_count": user.usable_count,
            "entry_token": entry_token,
            "remote": risk.remote,
            "request_count": risk.request_count,
            "window_seconds": risk.window_seconds,
            "session_mark": whitelist_username if (named_whitelisted or local_whitelisted) else user.username,
            "local_whitelist": local_whitelisted,
            "named_whitelist": named_whitelisted,
            "workspace_csv": request.app["workspace_csv_content"],
            **counts,
        }
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(request.app["auth"].session_ttl.total_seconds()),
        httponly=True,
        samesite="Lax",
        path="/",
    )
    return response


async def api_auth_me(request: web.Request) -> web.Response:
    user = auth_user(request)
    if user is None:
        return web.json_response({"authenticated": False}, status=401)
    return web.json_response(
        {
            "authenticated": True,
            "username": user.username,
            "usable_count": user.usable_count,
            **auth_session_counts(request.app, user.username),
        }
    )


async def api_auth_logout(request: web.Request) -> web.Response:
    request.app["auth"].logout(request.cookies.get(SESSION_COOKIE, ""))
    response = web.json_response({"ok": True})
    response.del_cookie(SESSION_COOKIE, path="/")
    return response


async def handle_rpc(request: web.Request, ws: web.WebSocketResponse, req: dict[str, Any]) -> dict[str, Any]:
    rpc_id = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}

    if req.get("jsonrpc") != "2.0":
        return jsonrpc.error(rpc_id, -32600, "Invalid JSON-RPC version")
    if method == "k12.inspect_at":
        access_token = str(params.get("access_token", "")).strip()
        operator_log = str(params.get("operator_log", ""))
        async def progress(payload: dict[str, Any]) -> None:
            await send_json(ws, jsonrpc.notification("k12.progress", payload))

        try:
            result = await request.app["k12"].inspect_access_token(access_token, operator_log, progress=progress)
        except Exception as exc:
            await progress({"stage": "error", "message": f"查询流程失败: {repr(exc)}", "data": {}, "time": jsonrpc.now_ms()})
            raise
        await send_json(ws, jsonrpc.notification("k12.report", result))
        return jsonrpc.result(rpc_id, result)
    if method == "k12.apply_workspaces":
        access_token = str(params.get("access_token", "")).strip()
        workspace_ids = params.get("workspace_ids") or []
        operator_log = str(params.get("operator_log", ""))
        stop_on_success = params.get("stop_on_success") is True
        if not isinstance(workspace_ids, list):
            return jsonrpc.error(rpc_id, -32602, "workspace_ids must be a list")

        async def progress(payload: dict[str, Any]) -> None:
            await send_json(ws, jsonrpc.notification("k12.apply_progress", payload))

        try:
            result = await request.app["k12"].apply_workspaces(
                access_token,
                [str(item) for item in workspace_ids],
                operator_log,
                progress=progress,
                stop_on_success=stop_on_success,
            )
        except Exception as exc:
            await progress({"stage": "error", "message": f"申请空间失败: {repr(exc)}", "data": {}, "time": jsonrpc.now_ms()})
            raise
        return jsonrpc.result(rpc_id, result)
    if method == "k12.save_log":
        result = request.app["k12"].save_operator_log(str(params.get("text", "")))
        await send_json(ws, jsonrpc.notification("k12.log", result))
        return jsonrpc.result(rpc_id, result)
    if method == "k12.latest":
        return jsonrpc.result(
            rpc_id,
            {
                "disabled": True,
                "message": "k12.latest 已禁用，避免跨会话读取全局最新报告",
            },
        )
    if method == "server.status":
        return jsonrpc.result(
            rpc_id,
            {
                "k12_proxy": request.app["k12_proxy"],
                "clients": len(request.app["clients"]),
            },
        )
    return jsonrpc.error(rpc_id, -32601, f"Method not found: {method}")


async def close_whitelist_ws_after_timeout(
    app: web.Application,
    ws: web.WebSocketResponse,
    token: str,
    username: str,
    timeout_state: dict[str, Any],
    client_timeout_seconds: int,
    server_timeout_seconds: int,
) -> None:
    while not ws.closed:
        await asyncio.sleep(WHITELIST_WS_TIMEOUT_CHECK_INTERVAL_SECONDS)
        if int(timeout_state.get("active_tasks") or 0) > 0:
            continue
        now = asyncio.get_running_loop().time()
        idle_started_at = float(timeout_state.get("idle_started_at") or now)
        idle_elapsed = float(timeout_state.get("idle_elapsed_seconds") or 0.0) + max(0.0, now - idle_started_at)
        if idle_elapsed < server_timeout_seconds:
            continue
        timeout_state["idle_elapsed_seconds"] = idle_elapsed
        break
    if ws.closed:
        return
    app["logger"].warning(
        "whitelist websocket timeout user=%s client_timeout=%s server_timeout=%s idle_elapsed=%.3f",
        username,
        client_timeout_seconds,
        server_timeout_seconds,
        float(timeout_state.get("idle_elapsed_seconds") or 0.0),
    )
    with suppress(Exception):
        await send_json(
            ws,
            jsonrpc.notification(
                "server.session_closed",
                {
                    "message": "白名单会话已到期，请重新登录",
                    "reason": "whitelist_session_timeout",
                    "client_timeout_seconds": client_timeout_seconds,
                    "server_timeout_seconds": server_timeout_seconds,
                },
            ),
        )
    with suppress(Exception):
        app["auth"].logout(token)
    with suppress(Exception):
        await ws.close(code=4001, message=b"whitelist session timeout")


def make_whitelist_timeout_state() -> dict[str, Any]:
    return {
        "active_tasks": 0,
        "idle_started_at": asyncio.get_running_loop().time(),
        "idle_elapsed_seconds": 0.0,
    }


def pause_whitelist_idle_timer(timeout_state: dict[str, Any] | None) -> None:
    if timeout_state is None:
        return
    active_tasks = int(timeout_state.get("active_tasks") or 0)
    now = asyncio.get_running_loop().time()
    if active_tasks == 0:
        idle_started_at = float(timeout_state.get("idle_started_at") or now)
        timeout_state["idle_elapsed_seconds"] = float(timeout_state.get("idle_elapsed_seconds") or 0.0) + max(
            0.0,
            now - idle_started_at,
        )
        timeout_state["idle_started_at"] = None
    timeout_state["active_tasks"] = active_tasks + 1


def resume_whitelist_idle_timer(timeout_state: dict[str, Any] | None) -> None:
    if timeout_state is None:
        return
    active_tasks = max(0, int(timeout_state.get("active_tasks") or 0) - 1)
    timeout_state["active_tasks"] = active_tasks
    if active_tasks == 0:
        timeout_state["idle_started_at"] = asyncio.get_running_loop().time()


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    if not is_allowed_ws_origin(request):
        request.app["logger"].warning(
            "blocked websocket origin remote=%s origin=%s host=%s allowed_origins=%s",
            request.remote,
            request.headers.get("Origin", ""),
            request.headers.get("Host", ""),
            ",".join(request.app["allowed_origins"]),
        )
        raise web.HTTPForbidden(text="forbidden origin")

    user = auth_user(request)
    if user is None:
        request.app["logger"].warning("blocked unauthenticated websocket remote=%s", request.remote)
        raise web.HTTPUnauthorized(text="login required")

    ws = web.WebSocketResponse()
    await ws.prepare(request)
    session_token = request.cookies.get(SESSION_COOKIE, "")
    whitelist_session = is_whitelist_account(user.username)
    timeout_config = whitelist_ws_timeout_config(request) if whitelist_session else {
        "profile": "",
        "client_timeout_seconds": 0,
        "server_timeout_seconds": 0,
    }
    whitelist_timeout_state = make_whitelist_timeout_state() if whitelist_session else None
    timeout_task: asyncio.Task[None] | None = None
    request.app["clients"].add(ws)
    request.app["client_users"][ws] = user.username
    if whitelist_session:
        timeout_task = asyncio.create_task(
            close_whitelist_ws_after_timeout(
                request.app,
                ws,
                session_token,
                user.username,
                whitelist_timeout_state,
                int(timeout_config["client_timeout_seconds"]),
                int(timeout_config["server_timeout_seconds"]),
            )
        )
    request.app["logger"].info(
        "frontend ws connected user=%s whitelist_session=%s timeout_profile=%s client_timeout=%s server_timeout=%s clients=%s",
        user.username,
        whitelist_session,
        timeout_config["profile"],
        timeout_config["client_timeout_seconds"],
        timeout_config["server_timeout_seconds"],
        len(request.app["clients"]),
    )

    try:
        await send_json(
            ws,
            jsonrpc.notification(
                "server.hello",
                {
                    "auth": {
                        "username": user.username,
                        "usable_count": user.usable_count,
                        **auth_session_counts(request.app, user.username),
                    },
                    "session": {
                        "whitelist": whitelist_session,
                        "timeout_mode": "idle" if whitelist_session else "",
                        "timeout_profile": timeout_config["profile"],
                        "client_timeout_seconds": timeout_config["client_timeout_seconds"],
                        "server_timeout_seconds": timeout_config["server_timeout_seconds"],
                    },
                    "k12_proxy": request.app["k12_proxy"],
                    "clients": len(request.app["clients"]),
                },
            ),
        )

        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                req: dict[str, Any] | None = None
                try:
                    req = json.loads(msg.data)
                    if auth_user(request) is None:
                        request.app["logger"].warning("blocked websocket rpc after session expired remote=%s", request.remote)
                        await send_json(
                            ws,
                            jsonrpc.error(req.get("id") if isinstance(req, dict) else None, -32001, "login required"),
                        )
                        await ws.close(code=1008, message=b"login required")
                        break
                    pause_whitelist_idle_timer(whitelist_timeout_state)
                    try:
                        payload = await handle_rpc(request, ws, req)
                    finally:
                        resume_whitelist_idle_timer(whitelist_timeout_state)
                except json.JSONDecodeError:
                    payload = jsonrpc.error(None, -32700, "Parse error")
                except Exception as exc:
                    request.app["logger"].exception("ws rpc error")
                    payload = jsonrpc.error(req.get("id") if isinstance(req, dict) else None, -32603, repr(exc))
                await send_json(ws, payload)
            elif msg.type == WSMsgType.ERROR:
                break
    finally:
        if timeout_task is not None and not timeout_task.done():
            timeout_task.cancel()
            with suppress(asyncio.CancelledError):
                await timeout_task
        request.app["clients"].discard(ws)
        request.app["client_users"].pop(ws, None)
        request.app["logger"].info(
            "frontend ws disconnected clients=%s close_code=%s exception=%r",
            len(request.app["clients"]),
            ws.close_code,
            ws.exception(),
        )
    return ws


async def start_background(app: web.Application) -> None:
    app["logger"] = setup_logging(app["log_dir"])
    app["workspace_csv_content"] = load_workspace_csv_content(app["workspace_csv_path"], app["logger"])
    created_default_user = app["auth"].initialize()
    app["logger"].info(
        "k12 server starting db_dir=%s log_dir=%s auth_db=%s proxy=%s allowed_origins=%s",
        app["db_dir"],
        app["log_dir"],
        app["auth_db"],
        app["k12_proxy"],
        ",".join(app["allowed_origins"]) or "-",
    )
    if created_default_user:
        app["logger"].info(
            "default auth user created username=%s usable_count=%s",
            app["auth_default_user"],
            app["auth_default_uses"],
        )
    app["k12"] = K12Service(
        app["db_dir"],
        log_dir=app["log_dir"],
        base_url=app["k12_base_url"],
        proxy=app["k12_proxy"],
        logger=app["logger"],
    )


async def cleanup_background(app: web.Application) -> None:
    logger = app["logger"]
    logger.info("server cleanup starting")
    logger.info("server cleanup done")
    access_logger = logging.getLogger("aiohttp.access")
    handlers = list(dict.fromkeys([*logger.handlers, *access_logger.handlers]))
    logger.handlers.clear()
    access_logger.handlers.clear()
    for handler in handlers:
        handler.flush()
        handler.close()


def create_app(
    log_dir: Path = DEFAULT_LOG_DIR,
    db_dir: Path = DEFAULT_DB_DIR,
    auth_db: Path = DEFAULT_AUTH_DB,
    auth_default_user: str = "admin",
    auth_default_password: str = "admin123456",
    auth_default_uses: int = 100,
    k12_base_url: str = "https://chatgpt.com",
    k12_proxy: str | None = DEFAULT_K12_PROXY,
    allowed_origins: tuple[str, ...] = DEFAULT_ALLOWED_ORIGINS,
    workspace_csv_path: Path = DEFAULT_WORKSPACE_CSV,
) -> web.Application:
    app = web.Application()
    app["db_dir"] = db_dir
    app["log_dir"] = log_dir
    app["auth_db"] = auth_db
    app["auth_default_user"] = auth_default_user
    app["auth_default_uses"] = auth_default_uses
    app["auth"] = AuthService(
        auth_db,
        default_username=auth_default_user,
        default_password=auth_default_password,
        default_usable_count=auth_default_uses,
    )
    app["k12_base_url"] = k12_base_url
    app["k12_proxy"] = normalize_k12_proxy(k12_proxy)
    app["workspace_csv_path"] = workspace_csv_path
    app["workspace_csv_content"] = ""
    app["allowed_origins"] = tuple(origin for origin in (normalize_origin(item) for item in allowed_origins) if origin)
    app["clients"] = set()
    app["client_users"] = {}
    app.router.add_get("/", index)
    app.router.add_get("/html/websocket", websocket_page)
    app.router.add_get("/html/js", js_page)
    app.router.add_get("/api/status", api_status)
    app.router.add_get("/api/auth/me", api_auth_me)
    app.router.add_post("/api/auth/query", api_auth_query)
    app.router.add_post("/api/auth/login", api_auth_login)
    app.router.add_post("/api/auth/logout", api_auth_logout)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/static/", STATIC_DIR, show_index=False)
    app.on_startup.append(start_background)
    app.on_cleanup.append(cleanup_background)
    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="K12 workspace application aiohttp dashboard server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--auth-db", type=Path, default=DEFAULT_AUTH_DB)
    parser.add_argument("--auth-default-user", default=os.getenv("K12_AUTH_USER", "admin"))
    parser.add_argument("--auth-default-password", default=os.getenv("K12_AUTH_PASSWORD", "admin123456"))
    parser.add_argument("--auth-default-uses", type=int, default=int(os.getenv("K12_AUTH_USES", "100")))
    parser.add_argument("--k12-base-url", default="https://chatgpt.com")
    parser.add_argument(
        "--k12-proxy",
        default=os.getenv("K12_PROXY", DEFAULT_K12_PROXY),
        help="HTTP proxy for K12 account queries; use none/direct/off/0/false/null or empty string for direct mode",
    )
    parser.add_argument("--no-k12-proxy", action="store_true", help="Disable K12 proxy and use direct outbound requests")
    parser.add_argument(
        "--allowed-origins",
        default=os.getenv("K12_ALLOWED_ORIGINS"),
        help="Comma-separated WebSocket Origin whitelist, e.g. https://example.com,https://www.example.com",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    web.run_app(
        create_app(
            log_dir=args.log_dir,
            db_dir=args.db_dir,
            auth_db=args.auth_db,
            auth_default_user=args.auth_default_user,
            auth_default_password=args.auth_default_password,
            auth_default_uses=args.auth_default_uses,
            k12_base_url=args.k12_base_url,
            k12_proxy=None if args.no_k12_proxy else args.k12_proxy,
            allowed_origins=parse_allowed_origins(args.allowed_origins),
        ),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
