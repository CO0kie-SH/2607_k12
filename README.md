# K12 空间申请 Web 工作台

当前版本：`26.7.4B`
最后更新：`2026-07-04`

这是一个基于 `aiohttp` 的 K12 空间申请 Web 工作台。主流程通过登录页进入 WebSocket 后端版页面，后端使用 `curl_cffi` 经过代理查询账号信息、申请空间、导出 JSON 报告并保存操作日志。

纯前端页面 `/html/js` 保留为实验入口；当前主流程是 `/html/websocket`。
AT / RT 与纯 JS 页面说明见：`doc/AT_RT_GUIDE.md`。

## 本版说明

`26.7.4B` 是当前版本，主线功能为：账号登录、一次性工作台会话、WebSocket Origin 白名单、AT 查询、按邮箱后缀申请空间、重复申请前端拦截、申请后空间列表重试确认、操作日志落盘，以及两个浏览器辅助按钮。

本版“打开网页”用于打开 ChatGPT session 地址；“退出空间”只打开 ChatGPT 账号设置入口并记录当前 workspace ID，不调用后端退出空间 API。

本版将纯 JS 页面调整为只做 AT JWT payload 本地解析，不再由静态页面发起对外请求，避免浏览器跨域问题。workspace 停用状态目前没有可靠字段可判断，已在文档中记录边界。

## 核心能力

- 首页 `/` 提供账号名、密码输入框，以及“查询”“登录”按钮。
- 登录账号存储在 SQLite：`db/auth.sqlite3`。
- 密码使用 PBKDF2-HMAC-SHA256 加盐哈希，不明文保存。
- 账号包含 `usable_count` 可用次数字段。
- “查询”按钮校验账号密码后，前端显示 `remote`、近 60 秒请求次数 RPM 和账号可用次数。
- 完整 headers 只记录到后端服务日志，不返回前端。
- 请求事件写入 `db/auth.sqlite3`，当前按 `remote` 维度计算近 60 秒 RPM，后端同时保留 `X-Forwarded-For`、`X-Real-IP`、`Forwarded`、`User-Agent` 等风控维度。
- 登录成功后写入 `HttpOnly` Cookie：`k12_session`，并返回一次性工作台进入令牌。
- `/html/websocket` 必须携带未消费的一次性进入令牌；刷新工作台页面会丢弃当前会话并回到首页重新登录。
- 未登录访问 `/html/websocket` 会跳转回首页。
- `/ws` 会校验 WebSocket `Origin` 白名单，未登录或 Origin 不匹配都会被后端拒绝。
- 纯 JS 页面 `/html/js` 只解析 AT 自带 claims，不发起对外请求，不查询 workspace。
- WebSocket 版支持提交 AccessToken，后端查询 `/backend-api/me` 和 `/backend-api/accounts`。
- 查询报告导出到 `db/k12_<account_id>.json`。
- AT 查询摘要追加到 `db/k12_at_records.jsonl`。
- 操作日志保存到 `log/k12_operator.log`。
- 服务运行日志保存到 `log/k12_server.log`，使用 rotating log。
- 出站 HTTP 客户端统一封装在 `tool/`，主流程默认使用 `tool/curl_cffi_client.py`。
- 保留 `curl.exe` 兜底版本：`server/k12_service_curl.py`、`try_join_first_curl.py`。
- 空间 ID 支持 `workspace_id,plan_type,email_suffix,available` 行格式。
- 前端按 AT 邮箱后缀匹配可用空间后才申请。
- 前端会检查当前账号已有 workspace ID，匹配空间已存在时不向后端提交申请。
- “打开网页”按钮会打开 `https://chatgpt.com/api/auth/session`，用于浏览器侧检查 ChatGPT session。
- “退出空间”按钮会基于当前查询到的 workspace 列表记录操作日志，并打开 ChatGPT 账号设置入口；当前不调用后端退出空间 API。
- 后端会兜底规整 workspace 输入，只取 CSV 第一列 UUID。
- 申请流程按顺序执行 `request -> accept`，首个 accept 成功后停止。
- HTTP 2xx 都按成功处理，避免 `204 No Content` 被误判失败。

## 项目结构

```text
2607_k12/
├── main.py                         # aiohttp 服务入口
├── requirements.txt                # Python 依赖
├── README.md                       # 项目说明
├── doc/
│   ├── API.md                      # HTTP / WebSocket API 文档
│   └── AT_RT_GUIDE.md              # AT / RT、纯 JS 页面和空间状态边界说明
├── server/
│   ├── app.py                      # HTTP 路由、登录接口、WebSocket 和 JSON-RPC 分发
│   ├── auth_service.py             # SQLite 账号、密码、session 和风控计数
│   ├── k12_service.py              # K12 查询、空间申请、报告导出、日志保存
│   ├── k12_service_curl.py         # curl.exe 兜底版，默认不启用
│   └── jsonrpc.py                  # JSON-RPC 响应、错误、通知和 event_id 工具
├── static/
│   ├── index.html                  # 登录首页
│   ├── index.js                    # 登录/查询前端逻辑
│   ├── websocket.html              # WebSocket 后端版主页面
│   ├── websocket.js                # WebSocket 后端版交互逻辑
│   ├── js.html                     # 纯前端实验页
│   ├── js-only.js                  # 纯前端实验逻辑
│   └── style.css                   # 页面样式
├── tool/
│   ├── base_http_client.py         # HTTP client 统一结果封装
│   ├── curl_cffi_client.py         # 当前主流程 HTTP client
│   ├── curl_exe_client.py          # curl.exe 兼容 client
│   ├── aiohttp_client.py           # aiohttp 兼容 client
│   └── requests_client.py          # requests 历史兼容 client
├── db/
│   ├── auth.sqlite3                # 登录账号、session、可用次数
│   ├── k12_<account_id>.json       # K12 查询报告
│   └── k12_at_records.jsonl        # AT 查询摘要
└── log/
    ├── k12_server.log              # 服务日志
    └── k12_operator.log            # 页面操作日志
```

## 环境要求

- Windows / PowerShell
- Python 3.12，当前环境：`D:\0Code2\py312\python.exe`
- 依赖：`aiohttp>=3.9`、`curl_cffi>=0.15`
- 可选：Node.js，用于检查前端 JS 语法
- 默认代理：`http://127.0.0.1:7897`

安装依赖：

```powershell
cd D:\PycharmProjects\0github\2607_k12
& 'D:\0Code2\py312\python.exe' -m pip install -r requirements.txt
```

## 运行方式

默认运行：

```powershell
cd D:\PycharmProjects\0github\2607_k12
& 'D:\0Code2\py312\python.exe' main.py --host 127.0.0.1 --port 8088
```

指定代理：

```powershell
& 'D:\0Code2\py312\python.exe' main.py --host 127.0.0.1 --port 8088 --k12-proxy http://127.0.0.1:7897
```

不使用代理：

```powershell
& 'D:\0Code2\py312\python.exe' main.py --host 127.0.0.1 --port 8088 --k12-proxy ''
```

## 登录账号

首次启动时，如果 `db/auth.sqlite3` 中没有账号，会自动创建默认账号：

```text
账号：admin
密码：admin123456
可用次数：100
```

启动时可覆盖默认账号配置：

```powershell
& 'D:\0Code2\py312\python.exe' main.py `
  --auth-default-user admin `
  --auth-default-password "your-password" `
  --auth-default-uses 100
```

也可使用环境变量：

```text
K12_AUTH_USER
K12_AUTH_PASSWORD
K12_AUTH_USES
K12_ALLOWED_ORIGINS
```

注意：默认账号只会在账号表为空时创建。数据库已有账号后，修改启动参数不会覆盖旧账号。

## 命令行参数

```text
--host                    监听地址，默认 0.0.0.0
--port                    监听端口，默认 8088
--db-dir                  K12 JSON 报告和 AT 记录目录，默认 db
--log-dir                 日志目录，默认 log
--auth-db                 登录 SQLite 数据库路径，默认 db/auth.sqlite3
--auth-default-user        首次初始化默认账号名，默认 admin
--auth-default-password    首次初始化默认密码，默认 admin123456
--auth-default-uses        首次初始化默认可用次数，默认 100
--k12-base-url             K12 请求基础 URL，默认 https://chatgpt.com
--k12-proxy                K12 后端请求代理，默认 http://127.0.0.1:7897
--allowed-origins          WebSocket Origin 白名单，多个 Origin 用英文逗号分隔
```

WebSocket Origin 白名单默认只允许本地开发地址：

```text
http://127.0.0.1:8088
http://localhost:8088
http://[::1]:8088
```

公网部署时必须显式配置公网访问域名：

```powershell
& 'D:\0Code2\py312\python.exe' main.py `
  --host 127.0.0.1 `
  --port 8088 `
  --allowed-origins "https://your-domain.example"
```

## 页面入口

```text
首页/登录页：http://127.0.0.1:8088/
WebSocket 后端版：http://127.0.0.1:8088/html/websocket
纯 JS 实验版：http://127.0.0.1:8088/html/js
```

部署到局域网或服务器时，将 `127.0.0.1` 替换为实际访问地址。

## 登录和访问控制

登录链路：

```text
Browser /
  ├─ POST /api/auth/query  -> 查询账号并记录 remote 请求次数
  └─ POST /api/auth/login  -> 登录成功后写入 HttpOnly Cookie，并返回 entry_token

Browser /html/websocket?entry=<entry_token>
  ├─ 未登录、缺少 entry、entry 已消费：302 跳回 /
  └─ 已登录且 entry 未消费：消费 entry，返回 WebSocket 后端版页面

Browser /ws
  ├─ Origin 不在白名单：403 forbidden origin
  ├─ 未登录：401 login required
  └─ 已登录：建立 WebSocket，收到 server.hello；每次 RPC 前重新校验 session
```

当前版本只实现登录门槛、remote 近 60 秒 RPM 统计和可用次数查询；尚未在每次申请成功后扣减可用次数。后续限流可基于 `auth_risk_fingerprints` 和 `auth_risk_events` 中的多维指纹数据接入。

## WebSocket 版使用流程

1. 打开 `/`，输入账号名和密码。
2. 点击“查询”，查看当前 `remote`、近 60 秒 RPM 和账号可用次数。
3. 点击“登录”，携带一次性 `entry_token` 进入 `/html/websocket`。
4. 可点击“打开网页”，打开 `https://chatgpt.com/api/auth/session` 检查浏览器侧 ChatGPT session。
5. 输入 `eyJ...` 开头的 AccessToken。
6. 点击“提交查询”，后端查询账号信息并导出报告。
7. 查询成功后点击“申请空间”。
8. 前端按 AT 邮箱后缀过滤空间 ID，只提交匹配项。
9. 前端排除当前账号已存在的 workspace ID；如果匹配项全部已存在，不提交后端申请。
10. 后端逐个执行 `request -> accept`，成功一个即停止。
11. 申请后刷新空间列表；如果申请 ID 暂未出现，延迟 1 秒后重试，最多请求 3 次。
12. 如果 `request` 表面失败但刷新后空间已出现，流程会标记为 `confirmed_after_refresh`。
13. 可点击“退出空间”，打开 ChatGPT 账号设置入口，并在操作日志中记录当前账号 workspace ID。

默认空间 ID 列表：

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

## 数据流

```text
Browser /html/websocket
        │
        ├─ JSON-RPC(k12.inspect_at) ───────┐
        ├─ JSON-RPC(k12.apply_workspaces) ─┤
        │                                  ▼
        └────────────── /ws ───────> aiohttp server
                                           │
                                           ├─ curl_cffi + proxy -> /backend-api/me
                                           ├─ curl_cffi + proxy -> /backend-api/accounts
                                           ├─ curl_cffi + proxy -> /invites/request
                                           ├─ curl_cffi + proxy -> /invites/accept
                                           ├─ export JSON -> db/
                                           └─ save log -> log/
```

## API 摘要

HTTP API：

```text
GET  /                     登录首页
GET  /html/websocket       WebSocket 后端版页面，需登录
GET  /html/js              纯前端实验页
GET  /api/status           服务状态，需登录
GET  /api/auth/me          当前登录态
POST /api/auth/query       查询账号并返回 remote、RPM 和可用次数
POST /api/auth/login       登录并写入 session cookie，返回 entry_token
POST /api/auth/logout      退出登录
GET  /ws                   WebSocket JSON-RPC，需登录且 Origin 在白名单
```

WebSocket JSON-RPC：

```text
k12.inspect_at             查询 AT 账号信息并导出报告
k12.apply_workspaces       按顺序申请空间，成功一个即停，刷新列表最多重试 3 次
k12.save_log               保存页面操作日志
k12.latest                 读取最新 K12 查询报告
server.status              查询服务状态
```

详细协议见：`doc/API.md`。

## 风险说明

- 当前“已存在空间不重复申请”只在前端执行。
- 后端 `k12.apply_workspaces` 暂不校验申请 ID 是否已存在于当前账号。
- 如果绕过前端直接调用 WebSocket RPC，仍可能重复提交已有 workspace ID。
- 生产环境后续建议在后端申请前先查询当前 workspace 列表并做同样拦截。
- 当前“退出空间”按钮只打开网页入口并记录日志，不调用后端退出空间 API。
- 如果后续要自动退出空间，需要先确认官方接口、权限、请求方法和幂等规则，再接入后端校验。
- WebSocket 已增加 Origin 白名单；公网部署时必须把真实 HTTPS 域名写入 `--allowed-origins` 或 `K12_ALLOWED_ORIGINS`。
- 纯 JS 版本中的 AT 本地解析不需要联网，当前静态页面不再浏览器直连外部接口。
- 当前 `/backend-api/accounts` 返回字段不足以可靠判断 workspace 是否停用。
- `processor=stripe` 只能作为订阅/付费处理器线索，不能证明当前账号本人正在付费，也不能证明空间当前 active。
- `eligible_for_auto_reactivation=true` 不能直接等同于“已停用”或“可用”。

## 数据文件

### `db/auth.sqlite3`

登录功能数据库。核心表：

- `auth_users`：账号、密码盐、密码哈希、可用次数、启用状态、最后登录时间。
- `auth_sessions`：session token hash、一次性 entry token hash、账号、过期时间、最近访问时间、登录 headers 快照。
- `auth_risk_environments`：风控环境分组，后续可把多个指纹归并到同一环境。
- `auth_risk_fingerprints`：风控指纹累计计数，当前记录 remote、转发 IP、真实 IP、Forwarded 和 User-Agent 等维度。
- `auth_risk_events`：请求事件表，用于计算近 60 秒 RPM。

### `db/k12_<account_id>.json`

每次查询导出的 K12 账号报告。核心字段：

- `generated_at`
- `access_token_sha256`
- `access_token_preview`
- `decoded`
- `query`
- `workspace_ids`
- `workspace_details`
- `workspace_count`
- `report_path`

### `db/k12_at_records.jsonl`

AT 查询摘要记录。

### `log/k12_operator.log`

页面操作日志。

### `log/k12_server.log`

服务运行日志。

## 验证记录

封版前已完成以下验证：

- `node --check static/index.js`
- `node --check static/js-only.js`
- `node --check static/websocket.js`
- `python -B -m py_compile server/app.py server/auth_service.py server/k12_service.py tool/base_http_client.py tool/curl_cffi_client.py main.py`
- 未登录访问 `/html/websocket` 返回 `302 /?session=expired`
- 未登录连接 `/ws` 返回 `401`
- `/api/auth/query` 不返回 headers，只返回 `remote`、近 60 秒 RPM 和可用次数
- `/api/auth/login` 可写入登录态并返回一次性 `entry_token`
- 登录后携带未消费的 `entry_token` 访问 `/html/websocket` 返回 `200`
- 重复访问或刷新同一个 `/html/websocket?entry=...` 会删除 session 并返回首页
- `/ws` 缺失 `Origin` 或 Origin 不在白名单时返回 `403`
- `/ws` 携带白名单 Origin 且已登录时收到 `server.hello`
- 登录后连接 `/ws` 收到 `server.hello`
- 纯 JS 页面不再包含浏览器 `fetch` 外部接口请求，只做 AT JWT payload 本地解析
- 申请空间路径使用 `curl_cffi`
- 完整 CSV 空间行会规整为纯 UUID 后再拼接申请 URL
- HTTP `202/204` 会按成功处理，`403` 仍按失败处理

## 版本

当前版本：`26.7.4B`
更新日期：`2026-07-04`

## 更新日志

### 26.7.4B (2026-07-04)

- 调整：纯 JS 页面 `/html/js` 只做 AT JWT payload 本地解析，不再请求外部接口。
- 修复：纯 JS 页面文案从“浏览器直连查询”改为“不联网”，并更新静态脚本缓存版本号。
- 文档：补充 workspace 停用状态无法可靠判断的原因和字段边界。
- 说明：当前 `/backend-api/accounts` 返回字段不足以判断停用、订阅和账单状态；后续需要接入更明确的官方状态接口或稳定字段。

### 26.7.4A (2026-07-04)

- 新增：`/ws` WebSocket 握手 Origin 白名单校验。
- 新增：`--allowed-origins` 启动参数，支持英文逗号分隔多个允许的 Origin。
- 新增：`K12_ALLOWED_ORIGINS` 环境变量。
- 默认：只允许 `http://127.0.0.1:8088`、`http://localhost:8088`、`http://[::1]:8088` 三个本地开发 Origin。
- 调整：WebSocket 非法 Origin 返回 `403 forbidden origin`，不会进入登录态校验和 WebSocket 建连。
- 调整：服务启动日志输出当前生效的 `allowed_origins`。
- 验证：缺失 Origin、非法 Origin 均返回 `403`；白名单 Origin 可正常建立 WebSocket 并收到 `server.hello`。

### 26.7.3B (2026-07-03)

- 新增：WebSocket 工作台“打开网页”按钮，位置在“提交查询”左侧。
- 调整：“打开网页”目标地址为 `https://chatgpt.com/api/auth/session`。
- 新增：WebSocket 工作台“退出空间”按钮，位置在“申请空间”右侧。
- 新增：“退出空间”会检查当前查询结果中的 workspace ID，写入操作日志，并打开 ChatGPT 账号设置入口。
- 说明：“退出空间”当前不调用后端退出空间 API，避免使用未验证接口造成误操作。
- 调整：工作台按钮组样式，移动端按钮自动换行。
- 调整：首页登录响应缺少 `entry_token` 时，提示用户重启后端并确认前后端版本一致。
- 验证：`node --check static/index.js`、`node --check static/websocket.js`、`python -B -m py_compile server/app.py server/auth_service.py server/k12_service.py tool/base_http_client.py tool/curl_cffi_client.py main.py`。

### 26.7.3A (2026-07-03)

- 新增：首页登录表单，包含账号名和密码框。
- 新增：首页“查询”按钮，用于显示 `remote`、近 60 秒 RPM 和可用次数。
- 新增：首页“登录”按钮，登录成功后跳转到 WebSocket 后端版页面。
- 新增：工作台一次性 `entry_token`，刷新或重复访问工作台页面会删除当前 session 并回首页重新登录。
- 新增：`server/auth_service.py`，封装 SQLite 账号、密码、session 和风控计数逻辑。
- 新增：SQLite 数据库 `db/auth.sqlite3`。
- 新增：`auth_users` 表，保存账号、加盐密码哈希、可用次数和启用状态。
- 新增：`auth_sessions` 表，保存登录 session、一次性 entry token、过期时间、最近访问时间和 headers 快照。
- 新增：`auth_risk_environments`、`auth_risk_fingerprints` 和 `auth_risk_events` 表，保存多维风控指纹并计算近 60 秒 RPM。
- 调整：`/api/auth/query` 和 `/api/auth/login` 不再向前端返回 headers，headers 只写入服务端日志。
- 修复：申请空间后的账号列表刷新过快时，最多重试 3 次 `/backend-api/accounts`；若刷新后确认空间已加入，标记为 `confirmed_after_refresh`。
- 新增：前端申请前检查当前账号已有 workspace ID，已存在则不提交后端申请。
- 文档：记录“重复申请拦截仅在前端，后端暂不校验”的风险点。
- 新增：默认账号初始化逻辑，账号表为空时创建 `admin / admin123456 / 100`。
- 新增：`--auth-db`、`--auth-default-user`、`--auth-default-password`、`--auth-default-uses` 启动参数。
- 新增：`K12_AUTH_USER`、`K12_AUTH_PASSWORD`、`K12_AUTH_USES` 环境变量支持。
- 新增：`GET /api/auth/me`、`POST /api/auth/query`、`POST /api/auth/login`、`POST /api/auth/logout`。
- 新增：未登录访问 `/html/websocket` 时跳转回首页。
- 新增：未登录建立 `/ws` 时后端返回 `401 login required`。
- 修复：`/api/status` 改为必须登录，避免未登录泄露最新 K12 报告摘要。
- 新增：WebSocket 页面退出登录按钮。
- 调整：`aiohttp` 日志 handler 在服务 cleanup 时显式释放，避免 Windows 下日志文件占用。
- 调整：SQLite 连接显式 commit/rollback/close，避免数据库文件句柄占用。
- 文档：重写 README 和 API 文档，记录当前封版功能和验证结果。

### 26.7.2B (2026-07-03)

- 新增：出站 HTTP 客户端统一封装到 `tool/`。
- 新增：`BaseHttpClient`、`RequestsHttpClient`、`AiohttpHttpClient`、`CurlExeHttpClient`、`CurlCffiHttpClient`。
- 调整：主流程默认使用 `curl_cffi`。
- 保留：`curl.exe` 兜底版 `server/k12_service_curl.py` 和 `try_join_first_curl.py`。
- 调整：`requirements.txt` 增加 `curl_cffi>=0.15`。

### 26.7.2A (2026-07-03)

- 新增：默认空间 ID 改为两行 CSV：
  - `255de4a6-96a4-430a-b660-358954424e79,k12,outlook.com,true`
  - `ff598c4d-ccaf-40c1-bfaa-cb94565764b1,k12,gmail.com,true`
- 新增：前端按 AT 邮箱后缀匹配空间 ID 后才申请。
- 新增：后端 `_normalize_workspace_ids()`，兼容旧前端或手动 RPC 传入完整 CSV 行。
- 调整：前端脚本增加 query version，降低浏览器缓存旧 JS 的影响。
- 修复：申请 URL 错误拼入 `,k12,outlook.com,true` 的问题。
- 修复：HTTP 2xx 统一判定成功，避免 `204 No Content` 被误判失败。

### 26.7.1A (2026-07-02)

- 新增：K12 空间申请 Web 工作台主题。
- 新增：首页 `/`、WebSocket 后端版 `/html/websocket`、纯 JS 实验版 `/html/js`。
- 新增：AccessToken 输入、账号信息展示、空间 ID 输入和操作日志。
- 新增：`k12.inspect_at` JSON-RPC 方法。
- 新增：`k12.apply_workspaces` JSON-RPC 方法。
- 新增：账号查询报告导出到 `db/k12_<account_id>.json`。
- 新增：AT 查询摘要追加到 `db/k12_at_records.jsonl`。
- 新增：查询进度、申请进度和日志保存通知。
- 调整：操作日志写入 `log/k12_operator.log`。
- 调整：服务日志写入 `log/k12_server.log`。
