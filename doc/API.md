# K12 工作台 API 文档

本项目使用 `aiohttp` 提供 HTTP 页面、静态资源和 WebSocket JSON-RPC。当前主流程是 K12 账号查询和空间申请；旧 Gate 行情流相关 API 保留为兼容入口，默认不启动。

## 路由

| 路径 | 说明 |
| --- | --- |
| `/` | 首页入口，跳转到 WebSocket 版或纯前端版 |
| `/html/websocket` | K12 WebSocket 后端版主页面 |
| `/html/js` | 纯前端实验版页面 |
| `/control` | 兼容控制页 |
| `/ws` | WebSocket JSON-RPC |
| `/api/status` | 服务状态、代理配置、最新 K12 报告摘要 |
| `/static/*` | 静态资源 |

## 运行配置

默认参数：

```text
host=0.0.0.0
port=8088
db_dir=./db
log_dir=./log
k12_base_url=https://chatgpt.com
k12_proxy=http://127.0.0.1:7897
offline=true
```

启动示例：

```powershell
& 'D:\0Code2\py312\python.exe' main.py --host 127.0.0.1 --port 8088
```

## WebSocket 协议

WebSocket 地址：

```text
ws://127.0.0.1:8088/ws
```

客户端请求必须是 JSON-RPC 2.0：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "k12.inspect_at",
  "params": {}
}
```

服务端响应使用：

```json
{"jsonrpc":"2.0","id":1,"result":{}}
```

错误响应使用：

```json
{"jsonrpc":"2.0","id":1,"error":{"code":-32602,"message":"..."}}
```

服务端主动推送使用 notification：

```json
{"jsonrpc":"2.0","method":"k12.progress","params":{"stage":"...","message":"..."}}
```

所有响应和通知都会携带 `event_id`，便于日志追踪和回放。

## K12 RPC 方法

### `k12.inspect_at`

提交 AccessToken，后端完成解码、账号接口查询和 JSON 导出。

请求：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "k12.inspect_at",
  "params": {
    "access_token": "eyJ...",
    "operator_log": "[2026-07-02 18:48:24]查询eyJ******xxxxx的账号邮箱为xxx@xxx"
  }
}
```

后端动作：

- 校验 AT 以 `eyJ` 开头。
- 解码 JWT payload，提取邮箱、手机号、账号 ID、用户 ID、plan type。
- 通过代理请求：
  - `GET /backend-api/me`
  - `GET /backend-api/accounts`
- 提取 workspace ID 和空间详情。
- 写入 `db/k12_<account_id>.json`。
- 追加 AT 记录到 `db/k12_at_records.jsonl`。
- 如传入 `operator_log`，保存到 `log/k12_operator.log`。

响应摘要：

```json
{
  "report_path": "D:\\PycharmProjects\\k12_web\\db\\k12_<account_id>.json",
  "account_info": "生成时间: ...",
  "report": {
    "workspace_count": 2,
    "workspace_ids": ["..."],
    "workspace_details": [
      {"id":"...","type":"workspace","name":"...","role":"standard-user"}
    ]
  }
}
```

相关通知：

- `k12.progress`
- `k12.report`

### `k12.apply_workspaces`

提交 AT 和 workspace ID 列表，后端逐个申请空间，首个成功后停止。

请求：

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "k12.apply_workspaces",
  "params": {
    "access_token": "eyJ...",
    "workspace_ids": [
      "631e1603-06cf-4f0b-b79b-d09fbfcfe98d",
      "a65ebb2e-dd7c-4fdb-9a5d-6ccaf6ad00a3"
    ],
    "operator_log": "..."
  }
}
```

后端动作：

- 按列表顺序处理 workspace ID。
- 每次只处理一个 ID。
- 调用：
  - `POST /backend-api/accounts/{workspace_id}/invites/request`
  - `POST /backend-api/accounts/{workspace_id}/invites/accept`
- `request` 成功后等待 1.5 秒再 `accept`。
- `accept` 成功后等待 2 秒，再刷新账号信息。
- 成功一个即停，返回最终结果。
- 将申请过程追加保存到 `log/k12_operator.log`。

响应摘要：

```json
{
  "success": true,
  "stopped_by": "first_success",
  "accepted_workspace_id": "a65ebb2e-dd7c-4fdb-9a5d-6ccaf6ad00a3",
  "results": [
    {
      "workspace_id": "631e1603-06cf-4f0b-b79b-d09fbfcfe98d",
      "request_ok": false,
      "accept_ok": false,
      "status": "request_failed"
    },
    {
      "workspace_id": "a65ebb2e-dd7c-4fdb-9a5d-6ccaf6ad00a3",
      "request_ok": true,
      "accept_ok": true,
      "status": "accepted"
    }
  ],
  "account_report": {}
}
```

相关通知：

- `k12.apply_progress`

### `k12.save_log`

保存页面操作日志。

请求：

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "k12.save_log",
  "params": {
    "text": "[2026-07-02 18:48:24]..."
  }
}
```

响应：

```json
{
  "path": "D:\\PycharmProjects\\k12_web\\log\\k12_operator.log",
  "bytes": 123,
  "saved_at": "2026-07-02T15:23:54.933218+00:00"
}
```

相关通知：

- `k12.log`

### `k12.latest`

读取 `db/` 目录中最新的 `k12_*.json` 报告。

请求：

```json
{"jsonrpc":"2.0","id":4,"method":"k12.latest","params":{}}
```

## 服务端通知

| 方法 | 说明 |
| --- | --- |
| `server.hello` | WebSocket 连接后的初始化状态 |
| `k12.progress` | 查询 AT 的阶段进度 |
| `k12.report` | 查询完成后的账号报告 |
| `k12.apply_progress` | 申请空间的阶段进度 |
| `k12.log` | 日志保存结果 |

## 文件布局

```text
static/index.html       # 首页入口
static/websocket.html   # WebSocket 后端版页面
static/websocket.js     # WebSocket 后端版交互逻辑
static/js.html          # 纯前端实验版页面
static/js-only.js       # 纯前端实验版逻辑
server/app.py           # aiohttp 路由和 JSON-RPC 分发
server/k12_service.py   # K12 查询、申请、导出、日志保存
server/jsonrpc.py       # JSON-RPC 消息封装
db/                     # 查询报告和 AT 记录
log/                    # 服务日志和操作日志
```

## 输出文件

| 路径 | 说明 |
| --- | --- |
| `db/k12_<account_id>.json` | 查询报告，包含 decoded、query、workspace_ids、workspace_details |
| `db/k12_at_records.jsonl` | AT 查询记录 |
| `log/k12_operator.log` | 页面操作日志 |
| `log/k12_server.log` | 服务端运行日志 |

## 兼容说明

项目中仍保留旧 Gate 行情流代码：

- `server/gate_client.py`
- `server/stream_manager.py`
- `server/storage.py`
- `/api/kline/recent`
- `/api/kline/latest`
- `stream.*`
- `kline.*`

默认启动时 `offline=true`，不会启动 Gate WebSocket 客户端。只有传入 `--start-gate` 时才会启动旧行情流。
