# K12 工作台 API 文档

版本：`26.7.5R`
最后更新：`2026-07-05`

本项目使用 `aiohttp` 提供 HTTP 页面、静态资源、登录接口和 WebSocket JSON-RPC。当前主流程是：登录首页 -> WebSocket 后端版页面 -> K12 账号查询和空间申请。

## 路由总览

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/` | 否 | 登录首页 |
| `GET` | `/html/websocket` | Cookie + entry token | K12 WebSocket 后端版主页面 |
| `GET` | `/html/js` | 否 | 纯前端本地解析页 |
| `GET` | `/api/status` | Cookie | 服务状态 |
| `GET` | `/api/auth/me` | Cookie | 当前登录态 |
| `POST` | `/api/auth/query` | 账号密码或白名单账号 | 查询账号并返回 remote、RPM、会话数和可用次数 |
| `POST` | `/api/auth/login` | 账号密码或白名单账号 | 登录并写入 session cookie，返回一次性 entry token 和会话数 |
| `POST` | `/api/auth/logout` | Cookie | 退出登录 |
| `GET` | `/ws` | Cookie + Origin | WebSocket JSON-RPC |
| `GET` | `/static/*` | 否 | 静态资源 |

## 运行配置

AT / RT 与纯 JS 页面说明见：`doc/AT_RT_GUIDE.md`。

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
allowed_origins=http://127.0.0.1:8088,http://localhost:8088,http://[::1]:8088
```

启动示例：

```powershell
& 'D:\0Code2\py312\python.exe' main.py --host 127.0.0.1 --port 8088
```

公网反代部署时需要显式配置 WebSocket Origin 白名单：

```powershell
& 'D:\0Code2\py312\python.exe' main.py --host 127.0.0.1 --port 8088 --allowed-origins "https://your-domain.example"
```

公网环境不需要代理时，可以使用直连模式：

```powershell
& 'D:\0Code2\py312\python.exe' main.py --host 127.0.0.1 --port 8088 --k12-proxy none --allowed-origins "https://your-domain.example"
```

也可以使用 `--no-k12-proxy` 禁用代理。`--k12-proxy` 传入 `none`、`direct`、`off`、`0`、`false`、`null` 或空字符串时都会被识别为直连。

也可以使用环境变量：

```text
K12_ALLOWED_ORIGINS=https://your-domain.example
K12_PROXY=none
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

校验账号密码，前端返回有效 `remote`、近 60 秒请求次数 RPM、账号会话数和账号可用次数。完整 headers 会写入后端服务日志，不返回给前端。该接口会把有效 `remote`、`CF-Connecting-IP`、原始 socket remote、`Forwarded`、`User-Agent` 等维度写入 `auth_risk_fingerprints`，并在 `auth_risk_events` 中记录请求事件，便于后续扩展风控。

Cloudflare 部署下，服务端优先使用 `CF-Connecting-IP` 作为有效 `remote`。`X-Forwarded-For` 和 `X-Real-IP` 中的 CF IPv6 只保留在服务端 headers 日志中，不作为判断 IP，也不参与 RPM 的 `remote` 维度。

服务启动时会从 `auth_risk_fingerprints` 和 `auth_risk_events` 中移除历史 `x_forwarded_for` / `x_real_ip` 风控维度记录。

命名白名单账号：`username=im-run` 或 `username=linux.do` 时无需密码即可查询，响应会包含 `named_whitelist=true`，但仍会记录并返回 RPM。

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
  "window_seconds": 60,
  "active_session_count": 0,
  "websocket_session_count": 0
}
```

字段说明：

- `active_session_count`：SQLite 中该账号未过期的登录 session 数。
- `websocket_session_count`：当前后端进程内该账号正在连接的 WebSocket 数。

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

命名白名单账号：`username=im-run` 或 `username=linux.do` 时无需密码即可登录，服务端会创建 session，`session_mark` 为实际命中的白名单账号，响应仍返回 `remote`、`request_count` 和 `window_seconds`。

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
  "workspace_csv": "ff598c4d-ccaf-40c1-bfaa-cb94565764b1,k12,gmail.com,true",
  "remote": "127.0.0.1",
  "request_count": 2,
  "window_seconds": 60,
  "active_session_count": 1,
  "websocket_session_count": 0
}
```

响应 Cookie：

```text
Set-Cookie: k12_session=<token>; HttpOnly; Path=/; SameSite=Lax; Max-Age=43200
```

前端必须携带响应中的 `entry_token` 跳转工作台。当前首页登录成功后不会自动跳转，会先在信息框显示 RPM，等待 3 秒后启用“进入工作台”按钮，由用户手动进入：

```text
/html/websocket?entry=<entry_token>
```

`workspace_csv` 来自服务启动时读取的根目录 `k12.csv` 内存缓存。登录接口不会每次重新读取文件；修改 `k12.csv` 后必须重启服务才会更新下发内容。首页会将该字段暂存到 `sessionStorage`，WebSocket 页面加载后填入空间 ID 输入框。若 `k12.csv` 包含 `workspace_id,...` 表头，服务端会在缓存时自动过滤，响应只下发空间记录。

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
  "usable_count": 100,
  "active_session_count": 1,
  "websocket_session_count": 0
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

直连模式下 `k12_proxy` 为 `null`。

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

### `GET /html/js`

纯前端本地解析页，不经过本项目后端代理，也不发起对外网络请求。

该页面当前只保留一类行为：

- 本地解析 AT 的 JWT payload，不需要联网。

旧版曾尝试使用浏览器直接请求官方接口；当前版本已移除该行为，避免静态页面受 CORS、浏览器登录态和网络环境影响。

详细说明见：`doc/AT_RT_GUIDE.md`。

### `GET /ws`

必须携带有效 `k12_session` Cookie，且 WebSocket 握手请求的 `Origin` 必须在服务端白名单中。

Origin 不匹配时：

```text
403 forbidden origin
```

未登录时：

```text
401 login required
```

已登录时建立 WebSocket，并立即收到 `server.hello`。WebSocket 建立后，每次 JSON-RPC 请求前都会重新检查当前 session；如果 session 已被删除或过期，服务端返回 `login required` 并关闭连接。

白名单账号或本机白名单登录进入 WebSocket 后，会启用空闲限时会话：普通连接前端根据 `server.hello.params.session.client_timeout_seconds=600` 在累计空闲 600 秒后主动退出；如果前端没有断开，后端会在累计空闲 `server_timeout_seconds=610` 后删除 session 并用关闭码 `4001` 关闭连接。`Host=127.0.0.1` 或有效客户端 IP 为 `127.0.0.1` 时使用本地超时档，前端 1800 秒主动断开，后端 1810 秒兜底断开。查询、申请、保存日志等 RPC 任务执行期间，前端和后端都暂停累计空闲时间。

服务端不启用 aiohttp WebSocket heartbeat，避免长 RPC 执行期间未读取 pong 导致连接被误判断开；会话回收由上述空闲计时和每次 RPC 前的 session 校验负责。

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
      "usable_count": 100,
      "active_session_count": 1,
      "websocket_session_count": 1
    },
    "session": {
      "whitelist": false,
      "timeout_mode": "",
      "timeout_profile": "",
      "client_timeout_seconds": 0,
      "server_timeout_seconds": 0
    },
    "k12_proxy": "http://127.0.0.1:7897",
    "k12_latest": {},
    "clients": 1
  }
}
```

直连模式下 `k12_proxy` 为 `null`。

白名单登录时 `session.whitelist=true`，`timeout_mode=idle`。普通连接返回 `timeout_profile=default`、`client_timeout_seconds=600`、`server_timeout_seconds=610`；`Host=127.0.0.1` 或有效客户端 IP 为 `127.0.0.1` 时返回 `timeout_profile=local_127`、`client_timeout_seconds=1800`、`server_timeout_seconds=1810`。后端兜底关闭前会尝试推送 `server.session_closed` notification。

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

隐私说明：该响应不会返回本地报告文件路径，WebSocket 帧中也不会包含 `report_path`。

空间状态判断边界：

- `workspace_details` 当前只按官方返回记录可见字段。
- 已观察到的字段包括 `id`、`name`、`structure`、`processor`、`current_user_role`、`eligible_for_auto_reactivation`、`created_time`。
- 当前未观察到稳定的 `status`、`disabled`、`suspended`、`active`、`subscription_status`、`billing_status`、`plan_status`、`deactivated_at`、`cancel_at` 等直接状态字段。
- 因此当前不能可靠判断哪个 workspace 已停用，也不能仅凭 `processor=stripe` 或 `eligible_for_auto_reactivation=true` 推断空间 active、停用或当前账号付费状态。

相关通知：

- `k12.progress`
- `k12.report`

### `k12.apply_workspaces`

提交 AT 和 workspace ID 列表，后端逐个申请空间。默认会继续尝试后续空间；传入 `stop_on_success=true` 时，首个成功后停止。

请求：

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "k12.apply_workspaces",
  "params": {
    "access_token": "eyJ...",
    "workspace_ids": [
      "b49cd6d8-b52d-4c21-93d7-89cc19b5e18e",
      "eb6642e8-b4a6-4652-9c18-67099f2781cc"
    ],
    "stop_on_success": false,
    "operator_log": "..."
  }
}
```

兼容说明：

- 后端会对 `workspace_ids` 做规整。
- 如果传入完整 CSV 行，例如 `id,k12,outlook.com,true`，只取第一列 UUID。
- 会自动去重并跳过空行。
- 前端会先排除当前账号已存在的 workspace ID；如果匹配项全部已存在，不调用该 RPC。
- 前端会检查当前 AT 的账号 ID 是否出现在待申请空间列表中；命中时提示“请用个人空间的AT进行申请”，但只跳过该 ID，其它候选空间继续调用该 RPC。
- 前端会检查查询结果是否出现 `deactivated_workspace`；命中时提示“请勿使用停用的空间进行申请”，不调用该 RPC。
- 如果账号自身 ID 和 `deactivated_workspace` 同时命中，前端会同时展示并记录两条提示，但仅 `deactivated_workspace` 会阻断 RPC。
- 风险点：以上过滤和拦截当前只在前端执行，后端暂不校验已有 workspace ID、账号 ID 或停用空间 ID。绕过前端直接调用 RPC 时，后端仍会按传入列表申请。

后端动作：

- 按列表顺序处理 workspace ID。
- 每次只处理一个 ID。
- 调用：
  - `POST /backend-api/accounts/{workspace_id}/invites/request`
  - `POST /backend-api/accounts/{workspace_id}/invites/accept`
- `request` 成功后等待 1.5 秒再 `accept`。
- `accept` 成功后等待 2 秒；默认继续尝试后续空间，`stop_on_success=true` 时停止后续申请。
- 刷新账号空间列表时，如果申请 ID 暂未出现，延迟 1 秒后再次请求 `/backend-api/accounts`，最多请求 3 次。
- 如果 `request` 表面失败但刷新后确认空间已加入，返回 `stopped_by=confirmed_after_refresh`。
- 前端会额外对比申请前后的 workspace 列表；如果后端返回失败但刷新报告出现新增空间，页面结果栏用绿色提示新增空间 ID。
- 新增空间相关日志会使用 `■■■【新增空间】■■■` 标记；操作日志区域是 `textarea`，不支持单行富文本颜色。
- 默认不会成功即停；只有 `stop_on_success=true` 时成功一个即停。
- 将申请过程追加保存到 `log/k12_operator.log`。

前端调用该 RPC 时会按候选数量动态设置超时，最少 10 分钟、最多 45 分钟，避免长列表申请时本地 180 秒超时导致页面提前断开。

响应摘要：

```json
{
  "success": true,
  "stopped_by": "completed_with_success",
  "accepted_workspace_id": "b49cd6d8-b52d-4c21-93d7-89cc19b5e18e",
  "accepted_workspace_ids": [
    "b49cd6d8-b52d-4c21-93d7-89cc19b5e18e"
  ],
  "stop_on_success": false,
  "results": [
    {
      "workspace_id": "b49cd6d8-b52d-4c21-93d7-89cc19b5e18e",
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
  "ok": true,
  "bytes": 123,
  "saved_at": "2026-07-03T06:00:00.000000+00:00"
}
```

隐私说明：该响应不会返回本地日志文件路径，`k12.log` 通知同样不包含路径。

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

直连模式下 `k12_proxy` 为 `null`。

## 服务端通知

| 方法 | 说明 |
| --- | --- |
| `server.hello` | WebSocket 连接后的初始化状态 |
| `server.session_closed` | 白名单 WebSocket 会话后端兜底关闭前的通知 |
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
- 直连模式下 `proxy_used` 为空字符串。

## 输出文件

| 路径 | 说明 |
| --- | --- |
| `db/auth.sqlite3` | 登录账号、session、可用次数 |
| `db/k12_<account_id>.json` | 查询报告，包含 decoded、query、workspace_ids、workspace_details |
| `db/k12_at_records.jsonl` | AT 查询记录 |
| `k12.csv` | 空间 ID CSV，服务启动时读取并缓存，登录成功后下发给前端 |
| `log/k12_operator.log` | 页面操作日志 |
| `log/k12_server.log` | 服务端运行日志 |

## 前端空间 ID 格式

空间 ID 输入框支持：

```text
workspace_id,plan_type,email_suffix,available
```

静态 HTML 默认值为空。主流程登录成功后，后端会下发启动时缓存的 `k12.csv` 内容，前端进入 WebSocket 页面时自动填入该输入框。

表头处理：

- `k12.csv` 可以包含 `workspace_id,plan_type,email_suffix,available` 或类似表头。
- 服务端启动缓存时会自动去掉首列为 `workspace_id` 的表头行。
- 前端解析输入框时也会跳过表头，手动粘贴带表头内容不会参与申请匹配。

示例：

```text
b49cd6d8-b52d-4c21-93d7-89cc19b5e18e,k12,gmail.com,true,
eb6642e8-b4a6-4652-9c18-67099f2781cc,k12,gmail.com,true,
83bec9de-395a-44e6-9a30-189508c22b99,k12,gmail.com,true,
a0a16bc9-e1b1-45f0-b269-812b53f60121,k12,gmail.com,true,
5e4c9b31-1b4e-4887-839b-607597928d7c,k12,gmail.com,true,
ff598c4d-ccaf-40c1-bfaa-cb94565764b1,k12,gmail.com,true,
631e1603-06cf-4f0b-b79b-d09fbfcfe98d,k12,outlook.com,true,
a65ebb2e-dd7c-4fdb-9a5d-6ccaf6ad00a3,k12,outlook.com,true,
52fb9943-aa13-4959-92bc-fe5e81c9e7f0,k12,outlook.com,true,
d3c40646-82b0-42a5-a9e6-01819e5f66b2,k12,outlook.com,true,
a4ed7848-dc98-4510-b4f8-ee170aad52ce,k12,outlook.com,true,
c4d1df5b-81cd-445d-a5ea-4131a0fbb9d2,k12,outlook.com,true,
44a5d4e6-e463-4412-88f3-0c98290027b7,k12,outlook.com,true,
```

匹配规则：

- 第 1 列是实际提交给后端的 workspace ID。
- 第 3 列是邮箱后缀；为空或 `*` 表示任意邮箱后缀都可匹配。
- 第 4 列必须为 `true` 才会参与申请。
- 如果 AT 邮箱是 `xxx@outlook.com`，只申请第 3 列为 `outlook.com` 的行。
- 如果匹配后的空间 ID 包含当前 AT 的账号 ID，前端提示“请用个人空间的AT进行申请”，只跳过该 ID，并继续提交其它候选空间。
- 如果当前查询结果里出现 `deactivated_workspace`，前端提示“请勿使用停用的空间进行申请”并停止提交。

## 封版验证记录

已完成：

- `node --check static/index.js`
- `node --check static/js-only.js`
- `node --check static/websocket.js`
- `python -B -m py_compile server/app.py server/auth_service.py server/k12_service.py tool/base_http_client.py tool/curl_cffi_client.py main.py`
- 未登录访问 `/html/websocket` 返回 `302 /?session=expired`
- 未登录连接 `/ws` 返回 `401`
- `/api/auth/query` 不返回 headers，只返回 `remote`、近 60 秒 RPM、会话数和可用次数。
- `/api/auth/login` 登录成功并返回一次性 `entry_token`、会话数。
- `/api/auth/login` 响应包含启动时缓存的 `workspace_csv`。
- `username=im-run`、`username=linux.do` 无需密码即可查询和登录，仍返回 RPM 信息。
- K12 查询、保存日志和状态接口的前端响应不包含本地 `*.json` / `*.log` 路径。
- 登录后携带未消费 `entry_token` 访问 `/html/websocket` 返回 `200`。
- 重复访问或刷新同一个 `/html/websocket?entry=...` 会删除 session 并回到首页。
- `/ws` 缺失 `Origin` 或 Origin 不在白名单时返回 `403`。
- `/ws` 携带白名单 Origin 且已登录时收到 `server.hello`。
- 白名单 WebSocket 会话普通连接累计空闲 600 秒前端主动断开，后端累计空闲 610 秒兜底断开；本地 127.0.0.1 连接为 1800/1810 秒。
- 登录后连接 `/ws` 收到 `server.hello`。
- 工作台页面包含“打开网页”和“退出空间”按钮。
- “打开网页”目标为 `https://chatgpt.com/api/auth/session`。
- “退出空间”只打开账号设置入口并写入操作日志，不调用后端退出空间 API。
- 纯 JS 页面 `/html/js` 不再发起外部请求，只做 AT JWT payload 本地解析。
