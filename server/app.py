from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from . import jsonrpc
from .k12_service import K12Service

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "static"
DEFAULT_DB_DIR = ROOT / "db"
DEFAULT_LOG_DIR = ROOT / "log"
DEFAULT_K12_PROXY = "http://127.0.0.1:7897"


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
    logging.getLogger("aiohttp.access").addHandler(file_handler)
    logging.getLogger("aiohttp.access").setLevel(logging.INFO)
    return logger


async def send_json(ws: web.WebSocketResponse, payload: dict[str, Any]) -> None:
    await ws.send_str(json.dumps(payload, ensure_ascii=False))


async def broadcast_rpc(app: web.Application, payload: dict[str, Any]) -> None:
    message = json.dumps(payload, ensure_ascii=False)
    app["logger"].info("broadcast method=%s event_id=%s clients=%s", payload.get("method"), payload.get("event_id"), len(app["clients"]))
    dead = []
    for ws in list(app["clients"]):
        if ws.closed:
            dead.append(ws)
            continue
        try:
            await ws.send_str(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        app["clients"].discard(ws)


async def index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


async def websocket_page(_: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "websocket.html")


async def js_page(_: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "js.html")


async def api_status(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "event_id": jsonrpc.make_event_id("http_status"),
            "k12_proxy": request.app["k12_proxy"],
            "k12_latest": request.app["k12"].latest_report(),
            "clients": len(request.app["clients"]),
        }
    )


async def handle_rpc(request: web.Request, req: dict[str, Any]) -> dict[str, Any]:
    rpc_id = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}

    if req.get("jsonrpc") != "2.0":
        return jsonrpc.error(rpc_id, -32600, "Invalid JSON-RPC version")
    if method == "k12.inspect_at":
        access_token = str(params.get("access_token", "")).strip()
        operator_log = str(params.get("operator_log", ""))
        async def progress(payload: dict[str, Any]) -> None:
            await broadcast_rpc(request.app, jsonrpc.notification("k12.progress", payload))

        try:
            result = await request.app["k12"].inspect_access_token(access_token, operator_log, progress=progress)
        except Exception as exc:
            await progress({"stage": "error", "message": f"查询流程失败: {repr(exc)}", "data": {}, "time": jsonrpc.now_ms()})
            raise
        await broadcast_rpc(request.app, jsonrpc.notification("k12.report", result))
        return jsonrpc.result(rpc_id, result)
    if method == "k12.apply_workspaces":
        access_token = str(params.get("access_token", "")).strip()
        workspace_ids = params.get("workspace_ids") or []
        operator_log = str(params.get("operator_log", ""))
        if not isinstance(workspace_ids, list):
            return jsonrpc.error(rpc_id, -32602, "workspace_ids must be a list")

        async def progress(payload: dict[str, Any]) -> None:
            await broadcast_rpc(request.app, jsonrpc.notification("k12.apply_progress", payload))

        try:
            result = await request.app["k12"].apply_workspaces(
                access_token,
                [str(item) for item in workspace_ids],
                operator_log,
                progress=progress,
            )
        except Exception as exc:
            await progress({"stage": "error", "message": f"申请空间失败: {repr(exc)}", "data": {}, "time": jsonrpc.now_ms()})
            raise
        return jsonrpc.result(rpc_id, result)
    if method == "k12.save_log":
        result = request.app["k12"].save_operator_log(str(params.get("text", "")))
        await broadcast_rpc(request.app, jsonrpc.notification("k12.log", result))
        return jsonrpc.result(rpc_id, result)
    if method == "k12.latest":
        return jsonrpc.result(rpc_id, request.app["k12"].latest_report())
    if method == "server.status":
        return jsonrpc.result(
            rpc_id,
            {
                "k12_proxy": request.app["k12_proxy"],
                "k12_latest": request.app["k12"].latest_report(),
                "clients": len(request.app["clients"]),
            },
        )
    return jsonrpc.error(rpc_id, -32601, f"Method not found: {method}")


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=120)
    await ws.prepare(request)
    request.app["clients"].add(ws)
    request.app["logger"].info("frontend ws connected clients=%s", len(request.app["clients"]))

    await send_json(
        ws,
        jsonrpc.notification(
            "server.hello",
            {
                "k12_proxy": request.app["k12_proxy"],
                "k12_latest": request.app["k12"].latest_report(),
                "clients": len(request.app["clients"]),
            },
        ),
    )

    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            req: dict[str, Any] | None = None
            try:
                req = json.loads(msg.data)
                payload = await handle_rpc(request, req)
            except json.JSONDecodeError:
                payload = jsonrpc.error(None, -32700, "Parse error")
            except Exception as exc:
                request.app["logger"].exception("ws rpc error")
                payload = jsonrpc.error(req.get("id") if isinstance(req, dict) else None, -32603, repr(exc))
            await send_json(ws, payload)
        elif msg.type == WSMsgType.ERROR:
            break

    request.app["clients"].discard(ws)
    request.app["logger"].info("frontend ws disconnected clients=%s", len(request.app["clients"]))
    return ws


async def start_background(app: web.Application) -> None:
    app["logger"] = setup_logging(app["log_dir"])
    app["logger"].info(
        "k12 server starting db_dir=%s log_dir=%s proxy=%s",
        app["db_dir"],
        app["log_dir"],
        app["k12_proxy"],
    )
    app["k12"] = K12Service(
        app["db_dir"],
        log_dir=app["log_dir"],
        base_url=app["k12_base_url"],
        proxy=app["k12_proxy"],
        logger=app["logger"],
    )


async def cleanup_background(app: web.Application) -> None:
    app["logger"].info("server cleanup starting")
    app["logger"].info("server cleanup done")


def create_app(
    log_dir: Path = DEFAULT_LOG_DIR,
    db_dir: Path = DEFAULT_DB_DIR,
    k12_base_url: str = "https://chatgpt.com",
    k12_proxy: str | None = DEFAULT_K12_PROXY,
) -> web.Application:
    app = web.Application()
    app["db_dir"] = db_dir
    app["log_dir"] = log_dir
    app["k12_base_url"] = k12_base_url
    app["k12_proxy"] = k12_proxy
    app["clients"] = set()
    app.router.add_get("/", index)
    app.router.add_get("/html/websocket", websocket_page)
    app.router.add_get("/html/js", js_page)
    app.router.add_get("/api/status", api_status)
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
    parser.add_argument("--k12-base-url", default="https://chatgpt.com")
    parser.add_argument("--k12-proxy", default=DEFAULT_K12_PROXY, help="HTTP proxy for K12 account queries")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    web.run_app(
        create_app(
            args.log_dir,
            db_dir=args.db_dir,
            k12_base_url=args.k12_base_url,
            k12_proxy=args.k12_proxy,
        ),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
