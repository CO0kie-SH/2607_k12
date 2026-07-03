#!/usr/bin/env python3
"""
传入 AT，依次尝试加入 k12.csv 中的 workspace，一旦成功立即停止并导出账号报告。

用法:
    python try_join_first.py --at "eyJhbG..."
    python try_join_first.py --at "eyJhbG..." -o k12_web

流程:
    1. 读取 k12.csv 中所有 available=true 的 workspace
    2. 逐一尝试 request+accept，成功一个就停止
    3. 成功后导出 AT 账号报告到 k12_web/
"""

import argparse
import asyncio
import base64
import csv
import json
import random
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from server.k12_service_curl import K12Service

SCRIPT_DIR = Path(__file__).resolve().parent
BASE = "https://chatgpt.com"
PROXY = "http://127.0.0.1:7897"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/135.0.0.0 Safari/537.36"
TIMEOUT = 3
INTERVAL = 1.5


def api_headers(at: str) -> dict[str, str]:
    return {
        "accept": "*/*",
        "authorization": f"Bearer {at}",
        "content-type": "application/json",
        "oai-device-id": str(uuid.uuid4()),
        "user-agent": UA,
    }


async def api_get(service: K12Service, at: str, path: str) -> dict | None:
    result = await service._request_json(f"{BASE}{path}", api_headers(at))
    return result.get("data") if result.get("ok") else None


async def api_post(service: K12Service, at: str, path: str) -> bool:
    result = await service._post_empty(f"{BASE}{path}", at)
    return bool(result.get("ok"))


def decode_at(at: str) -> dict:
    try:
        parts = at.split(".")
        b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
        jwt = json.loads(base64.urlsafe_b64decode(b64))
        auth = jwt.get("https://api.openai.com/auth", {})
        profile = jwt.get("https://api.openai.com/profile", {})
        return {
            "email": profile.get("email", ""),
            "phone": profile.get("phone_number", ""),
            "plan_type": auth.get("chatgpt_plan_type", ""),
            "account_id": auth.get("chatgpt_account_id", ""),
        }
    except Exception:
        return {}


def read_k12_ids() -> list[str]:
    path = SCRIPT_DIR / "k12.csv"
    if not path.exists():
        return []
    ids = []
    with open(path, encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            available = row[3].strip().lower() if len(row) > 3 else ""
            if available == "true" and row[0].strip():
                ids.append(row[0].strip())
    return ids


async def export_report(service: K12Service, at: str, out_dir: Path) -> None:
    info = decode_at(at)
    account_id = info.get("account_id", "unknown")
    email = info.get("email", "")

    # API 数据
    me_data = await api_get(service, at, "/backend-api/me") or {}
    accounts_data = await api_get(service, at, "/backend-api/accounts") or {}

    # 提取 workspace ID
    ws_ids = set()
    def walk(obj, depth=0):
        if depth > 5: return
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "id" and isinstance(v, str) and len(v) == 36 and v.count("-") == 4:
                    ws_ids.add(v)
                walk(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj: walk(item, depth + 1)
    walk(accounts_data)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "email": email,
        "phone": info.get("phone", ""),
        "plan_type": info.get("plan_type", ""),
        "account_id": account_id,
        "workspace_ids": sorted(ws_ids),
        "workspace_count": len(ws_ids),
        "me": me_data,
        "accounts": accounts_data,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{account_id}.json" if account_id else "_report.json"
    out_file = out_dir / fname
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[OK] 报告已保存: {out_file}")


async def main_async() -> None:
    parser = argparse.ArgumentParser(description="k12 快速加入脚本：成功一个就停")
    parser.add_argument("--at", required=True, help="AccessToken")
    parser.add_argument("-o", "--out-dir", default=str(SCRIPT_DIR / "k12_web"))
    args = parser.parse_args()

    at = args.at.strip()
    if not at.startswith("eyJ"):
        sys.exit("无效 AT")

    info = decode_at(at)
    email = info.get("email", "") or info.get("phone", "") or "?"
    print(f"账号: {email} [{info.get('plan_type', '?')}]")

    ws_ids = read_k12_ids()
    if not ws_ids:
        print("k12.csv 中无可用 workspace")
        return

    random.shuffle(ws_ids)

    print(f"随机尝试加入 {len(ws_ids)} 个 workspace（成功一个就停）:\n")

    service = K12Service(SCRIPT_DIR / "db", log_dir=SCRIPT_DIR / "log", base_url=BASE, proxy=PROXY, timeout=15)
    for i, ws_id in enumerate(ws_ids, 1):
        short = ws_id[:20] + "..."

        # request
        if not await api_post(service, at, f"/backend-api/accounts/{ws_id}/invites/request"):
            print(f"  [{i}/{len(ws_ids)}] {short} X")
            continue
        await asyncio.sleep(INTERVAL)

        # accept
        if await api_post(service, at, f"/backend-api/accounts/{ws_id}/invites/accept"):
            print(f"  [{i}/{len(ws_ids)}] {short} OK  -> 停止")
            # 导出报告
            await export_report(service, at, Path(args.out_dir))
            return
        print(f"  [{i}/{len(ws_ids)}] {short} request OK but accept X")

    print(f"\n全部失败")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
