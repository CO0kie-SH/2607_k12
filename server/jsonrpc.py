from __future__ import annotations

import itertools
import time
from typing import Any

_counter = itertools.count(1)


def now_ms() -> int:
    return int(time.time() * 1000)


def make_event_id(prefix: str = "evt") -> str:
    # unix time in ms + monotonic process counter; compact, sortable, and good for replay.
    return f"{now_ms()}-{prefix}-{next(_counter):06d}"


def notification(method: str, params: dict[str, Any], *, event_id: str | None = None) -> dict[str, Any]:
    eid = event_id or make_event_id(method.replace('.', '_'))
    return {"jsonrpc": "2.0", "event_id": eid, "method": method, "params": {"event_id": eid, **params}}


def result(rpc_id: Any, value: Any, *, event_id: str | None = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "event_id": event_id or make_event_id("result"), "id": rpc_id, "result": value}


def error(rpc_id: Any, code: int, message: str, *, event_id: str | None = None) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "event_id": event_id or make_event_id("error"),
        "id": rpc_id,
        "error": {"code": code, "message": message},
    }
