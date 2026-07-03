# K12 工作台 API 文档

版本：`26.7.3B`  
最后更新：`2026-07-03`

本项目使用 `aiohttp` 提供 HTTP 页面、静态资源、登录接口和 WebSocket JSON-RPC。当前主流程是：登录首页 -> WebSocket 后端版页面 -> K12 账号查询和空间申请。

## 路由总览

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/` | 否 | 登录首页 |
| `GET` | `/html/websocket` | Cookie + entry token | K12 WebSocket 后端版主页面 |
| `GET` | `/html/js` | 否 | 纯前端实验页 |
| `GET` | `/api/status` | Cookie | 服务状态 |
| `GET` | `/api/auth/me` | Cookie | 当前登录态 |
| `POST` | `/api/auth/query` | 账号密码 | 查询账号并返回 remote、RPM 和可用次数 |
| `POST` | `/api/auth/login` | 账号密码 | 登录并写入 session cookie，返回一次性 entry token |
| `POST` | `/api/auth/logout` | Cookie | 退出登录 |
| `GET` | `/ws` | 是 | WebSocket JSON-RPC |
| `GET` | `/static/*` | 否 | 静态资源 |

## 运行配置

默认参数：

```text
host=0.0.0.0
port=8088
db_dir=./db
log_dir=./log
auth_db=./db/auth.sqlite3
auth_default_user=admin
auth_default_password=admin123456
auth_default_uses=100
k12_base_url=https://chatgpt.com
k12_proxy=http://127.0.0.1:7897
```

启动示例：

```powershell
& 'D:\0Code2\py312\python.exe' main.py --host 127.0.0.1 --port 8088
```

## 登录和会话

### SQLite 表

登录数据库默认路径：

```text
db/auth.sqlite3
```

核心表：

```text
auth_users
  id
  username
  password_salt
  password_hash
  usable_count
  is_active
  created_at
  updated_at
  last_login_at

auth_sessions
  id
  token_hash
  username
  entry_token_hash
  entry_consumed_at
  created_at
  expires_at
  last_seen_at
  remote
  user_agent
  headers_json

auth_risk_environments
  environment_key
  label
  created_at
  updated_at

auth_risk_fingerprints
  id
  environment_key
  dimension
  value
  value_hash
  request_count
  first_seen_at
  last_seen_at

auth_risk_events
  id
  environment_key
  dimension
  value_hash
  seen_at
```

密码使用 PBKDF2-HMAC-SHA256 加盐哈希。session token 和工作台 `entry_token` 都只保存 SHA256 hash，不保存原始 token。

### `POST /api/auth/query`

校验账号密码，前端返回 `remote`、近 60 秒请求次数 RPM 和账号可用次数。完整 headers 会写入后端服务日志，不返回给前端。该接口会把 `remote`、`X-Forwarded-For`、`X-Real-IP`、`Forwarded`、`User-Agent` 等维度写入 `auth_risk_fingerprints`，并在 `auth_risk_events` 中记录请求事件，便于后续扩展风控。

请求：

```json
{
  "username": "admin",
  "password": "admin123456"
}
```

成功响应：

```json
{
  "ok": true,
  "username": "admin",
  "usable_count": 100,
  "is_active": true,
  "remote": "127.0.0.1",
  "request_count": 1,
  "window_seconds": 60
}
```

失败响应：

```json
{
  "ok": false,
  "message": "账号或密码错误",
  "remote": "127.0.0.1",
  "request_count": 1,
  "window_seconds": 60
}
```

状态码：`401`。

说明：服务端日志会记录脱敏后的 headers，其中 `Authorization`、`Cookie`、`Set-Cookie` 会被移除。

### `POST /api/auth/login`

校验账号密码和可用次数。账号启用且 `usable_count > 0` 时登录成功。

请求：

```json
{
  "username": "admin",
  "password": "admin123456"
}
```

成功响应：

```json
{
  "ok": true,
  "username": "admin",
  "usable_count": 100,
  "entry_token": "one-time-entry-token",
  "remote": "127.0.0.1",
  "request_count": 2,
  "window_seconds": 60
}
```

响应 Cookie：

```text
Set-Cookie: k12_session=<token>; HttpOnly; Path=/; SameSite=Lax; Max-Age=43200
```

前端必须携带响应中的 `entry_token` 跳转工作台：

```text
/html/websocket?entry=<entry_token>
```

失败响应：

```json
{
  "ok": false,
  "message": "账号或密码错误，或可用次数不足",
  "remote": "127.0.0.1",
  "request_count": 2,
  "window_seconds": 60
}
```

状态码：`401`。

### `GET /api/auth/me`

查询当前 Cookie 是否已登录。

成功响应：

```json
{
  "authenticated": true,
  "username": "admin",
  "usable_count": 100
}
```

未登录响应：

```json
{
  "authenticated": false
}
```

状态码：`401`。

### `POST /api/auth/logout`

删除当前 session 并清除 Cookie。

响应：

```json
{
  "ok": true
}
```

### `GET /api/status`

读取服务状态。该接口需要有效 `k12_session` Cookie，未登录不返回 `k12_latest`。

未登录响应：

```json
{
  "authenticated": false,
  "message": "login required"
}
```

状态码：`401`。

已登录响应摘要：

```json
{
  "event_id": "1783005834935-http_status-000001",
  "auth": {
    "username": "admin",
    "usable_count": 100
  },
  "k12_proxy": "http://127.0.0.1:7897",
  "k12_latest": {},
  "clients": 0
}
```

## 页面鉴权

### `GET /html/websocket`

必须携带有效 `k12_session` Cookie，并在查询参数中携带登录接口返回的一次性 `entry_token`。

未登录、缺少 `entry_token`、`entry_token` 不匹配或 `entry_token` 已消费时：

```text
302 Location: /?session=expired
```

校验通过时服务端会消费 `entry_token`，返回页面：

```text
200 text/html
```

刷新工作台页面会重复请求同一个 `entry_token`，后端会判定该令牌已消费，删除当前 session 并回到首页。

### `GET /ws`

必须携带有效 `k12_session` Cookie。

未登录时：

```text
401 login required
```

已登录时建立 WebSocket，并立即收到 `server.hello`。WebSocket 建立后，每次 JSON-RPC 请求前都会重新检查当前 session；如果 session 已被删除或过期，服务端返回 `login required` 并关闭连接。

## 工作台前端按钮

### 打开网页

位置：`AT 信息` 面板，“提交查询”按钮左侧。

行为：

- 不调用后端接口。
- 在新标签页打开：

```text
https://chatgpt.com/api/auth/session
```

### 退出空间

位置：`账号信息` 面板，“申请空间”按钮右侧。

行为：

- 先检查当前页面是否已有查询结果中的 `workspace_ids`。
- 如果尚未查询到空间列表，前端提示先提交查询。
- 如果已有空间列表，写入操作日志，并在新标签页打开：

```text
https://chatgpt.com/#settings/Account
```

说明：该按钮当前不调用后端退出空间 API，避免使用未验证接口造成误操作。

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

服务端响应：

```json
{
  "jsonrpc": "2.0",
  "event_id": "1783005834935-result-000004",
  "id": 1,
  "result": {}
}
```

错误响应：

```json
{
  "jsonrpc": "2.0",
  "event_id": "1783005834935-error-000004",
  "id": 1,
  "error": {
    "code": -32602,
    "message": "..."
  }
}
```

服务端主动推送使用 notification：

```json
{
  "jsonrpc": "2.0",
  "event_id": "1783005834935-k12_progress-000004",
  "method": "k12.progress",
  "params": {
    "stage": "query_accounts",
    "message": "查询账号空间信息中: /backend-api/accounts"
  }
}
```

## WebSocket 初始化事件

### `server.hello`

连接成功后立即推送。

示例：

```json
{
  "jsonrpc": "2.0",
  "method": "server.hello",
  "params": {
    "auth": {
      "username": "admin",
      "usable_count": 100
    },
    "k12_proxy": "http://127.0.0.1:7897",
    "k12_latest": {},
    "clients": 1
  }
}
```

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
    "operator_log": "[2026-07-03 14:00:00]查询eyJ******xxxxx的账号邮箱为xxx@xxx"
  }
}
```

后端动作：

- 校验 AT 以 `eyJ` 开头。
- 解码 JWT payload，提取邮箱、手机号、账号 ID、用户 ID、plan type。
- 使用 `curl_cffi` 请求：
  - `GET /backend-api/me`
  - `GET /backend-api/accounts`
- 提取 workspace ID 和空间详情。
- 写入 `db/k12_<account_id>.json`。
- 追加 AT 记录到 `db/k12_at_records.jsonl`。
- 如传入 `operator_log`，保存到 `log/k12_operator.log`。

响应摘要：

```json
{
  "report_path": "D:\\PycharmProjects\\0github\\2607_k12\\db\\k12_<account_id>.json",
  "account_info": "生成时间: ...",
  "report": {
    "workspace_count": 2,
    "workspace_ids": ["..."],
    "workspace_details": [
      {
        "id": "...",
        "type": "workspace",
        "name": "...",
        "role": "standard-user"
      }
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
      "255de4a6-96a4-430a-b660-358954424e79",
      "ff598c4d-ccaf-40c1-bfaa-cb94565764b1"
    ],
    "operator_log": "..."
  }
}
```

兼容说明：

- 后端会对 `workspace_ids` 做规整。
- 如果传入完整 CSV 行，例如 `id,k12,outlook.com,true`，只取第一列 UUID。
- 会自动去重并跳过空行。
- 前端会先排除当前账号已存在的 workspace ID；如果匹配项全部已存在，不调用该 RPC。
- 风险点：该重复申请拦截当前只在前端执行，后端暂不校验已有 workspace ID。绕过前端直接调用 RPC 时，后端仍会按传入列表申请。

后端动作：

- 按列表顺序处理 workspace ID。
- 每次只处理一个 ID。
- 调用：
  - `POST /backend-api/accounts/{workspace_id}/invites/request`
  - `POST /backend-api/accounts/{workspace_id}/invites/accept`
- `request` 成功后等待 1.5 秒再 `accept`。
- `accept` 成功后等待 2 秒，再刷新账号信息。
- 刷新账号空间列表时，如果申请 ID 暂未出现，延迟 1 秒后再次请求 `/backend-api/accounts`，最多请求 3 次。
- 如果 `request` 表面失败但刷新后确认空间已加入，返回 `stopped_by=confirmed_after_refresh`。
- 成功一个即停。
- 将申请过程追加保存到 `log/k12_operator.log`。

响应摘要：

```json
{
  "success": true,
  "stopped_by": "first_success",
  "accepted_workspace_id": "255de4a6-96a4-430a-b660-358954424e79",
  "results": [
    {
      "workspace_id": "255de4a6-96a4-430a-b660-358954424e79",
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
    "text": "[2026-07-03 14:00:00]..."
  }
}
```

响应：

```json
{
  "path": "D:\\PycharmProjects\\0github\\2607_k12\\log\\k12_operator.log",
  "bytes": 123,
  "saved_at": "2026-07-03T06:00:00.000000+00:00"
}
```

相关通知：

- `k12.log`

### `k12.latest`

读取 `db/` 目录中最新的 `k12_*.json` 报告。

请求：

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "k12.latest",
  "params": {}
}
```

### `server.status`

读取服务状态。

请求：

```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "method": "server.status",
  "params": {}
}
```

响应摘要：

```json
{
  "k12_proxy": "http://127.0.0.1:7897",
  "k12_latest": {},
  "clients": 1
}
```

## 服务端通知

| 方法 | 说明 |
| --- | --- |
| `server.hello` | WebSocket 连接后的初始化状态 |
| `k12.progress` | 查询 AT 的阶段进度 |
| `k12.report` | 查询完成后的账号报告 |
| `k12.apply_progress` | 申请空间的阶段进度 |
| `k12.log` | 日志保存结果 |

## HTTP 状态约定

K12 出站请求由 `tool/base_http_client.py` 统一封装：

- `200 <= status < 300` 判定为成功。
- `403` 等非 2xx 判定为失败。
- JSON 查询接口如果返回非 JSON，会记录 `json_error`。
- 返回结构中包含 `proxy_used` 和 `transport`，用于确认代理和 HTTP client。

## 输出文件

| 路径 | 说明 |
| --- | --- |
| `db/auth.sqlite3` | 登录账号、session、可用次数 |
| `db/k12_<account_id>.json` | 查询报告，包含 decoded、query、workspace_ids、workspace_details |
| `db/k12_at_records.jsonl` | AT 查询记录 |
| `log/k12_operator.log` | 页面操作日志 |
| `log/k12_server.log` | 服务端运行日志 |

## 前端空间 ID 格式

空间 ID 输入框支持：

```text
workspace_id,plan_type,email_suffix,available
```

示例：

```text
255de4a6-96a4-430a-b660-358954424e79,k12,outlook.com,true
ff598c4d-ccaf-40c1-bfaa-cb94565764b1,k12,gmail.com,true
```

匹配规则：

- 第 1 列是实际提交给后端的 workspace ID。
- 第 3 列是邮箱后缀。
- 第 4 列必须为 `true` 才会参与申请。
- 如果 AT 邮箱是 `xxx@outlook.com`，只申请第 3 列为 `outlook.com` 的行。

## 封版验证记录

已完成：

- `node --check static/index.js`
- `node --check static/websocket.js`
- `python -B -m py_compile server/app.py server/auth_service.py server/k12_service.py tool/base_http_client.py tool/curl_cffi_client.py main.py`
- 未登录访问 `/html/websocket` 返回 `302 /?session=expired`
- 未登录连接 `/ws` 返回 `401`
- `/api/auth/query` 不返回 headers，只返回 `remote`、近 60 秒 RPM 和可用次数。
- `/api/auth/login` 登录成功并返回一次性 `entry_token`。
- 登录后携带未消费 `entry_token` 访问 `/html/websocket` 返回 `200`。
- 重复访问或刷新同一个 `/html/websocket?entry=...` 会删除 session 并回到首页。
- 登录后连接 `/ws` 收到 `server.hello`。
- 工作台页面包含“打开网页”和“退出空间”按钮。
- “打开网页”目标为 `https://chatgpt.com/api/auth/session`。
- “退出空间”只打开账号设置入口并写入操作日志，不调用后端退出空间 API。
