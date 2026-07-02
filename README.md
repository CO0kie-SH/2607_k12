# K12 空间申请 Web 工作台

一个基于 `aiohttp` 的 K12 空间申请 Web 工作台，支持 AccessToken 本地提交、后端代理查询账号信息、WebSocket JSON-RPC 实时进度推送、账号 JSON 报告导出、空间 ID 批量申请和操作日志落盘。

当前主功能是 `/html/websocket` WebSocket 后端版页面；纯前端版 `/html/js` 保留为实验入口，不作为当前主流程。项目保留原 Gate 框架中的 HTTP、WebSocket、JSON-RPC、SQLite、日志和兼容模块，但默认不启动 Gate 行情流。

## 核心能力
- 使用 `aiohttp` 提供 HTTP 页面、静态资源和 WebSocket 服务
- 首页 `/` 提供两个入口：WebSocket 后端版和纯 JS 实验版
- WebSocket 后端版路径为 `/html/websocket`
- 支持输入 `eyJ...` 开头的 AccessToken
- 后端解码 JWT payload，提取邮箱、手机号、账号 ID、用户 ID 和 plan type
- 后端通过默认代理 `http://127.0.0.1:7897` 查询账号接口
- 查询 `/backend-api/me` 获取账号基础信息
- 查询 `/backend-api/accounts` 获取空间列表和空间详情
- 导出账号查询报告到 `db/k12_<account_id>.json`
- 记录 AT 查询摘要到 `db/k12_at_records.jsonl`
- 提取 workspace ID、空间类型、空间名和用户角色
- 账号信息框显示 workspace 数量、ID 列表和空间详情
- 操作日志使用浏览器本地时间，精确到秒
- 查询日志自动脱敏 AccessToken，只保留 `eyJ******xxxxx`
- “提交查询”运行中置灰，完成、失败或超时后恢复
- “申请空间”初始禁用，账号查询成功后启用
- “申请空间”读取页面空间 ID 列表并传给后端
- 后端按 workspace ID 顺序逐个执行 `request -> accept`
- 每次只处理一个 workspace ID
- `request` 成功后等待 `1.5s` 再执行 `accept`
- 首个 `accept` 成功后停止后续申请
- 申请完成后重新查询账号信息并导出最新 JSON
- 查询进度和申请进度通过 `/ws` 实时推送到前端
- 全程使用 JSON-RPC 2.0 传输
- 每条 JSON-RPC 响应和通知带 `event_id`，便于复盘
- 操作日志保存到 `log/k12_operator.log`
- 服务运行日志保存到 `log/k12_server.log`
- 使用 rotating log，避免服务日志无限增长
- 保留旧 Gate 行情模块和早期采集脚本，方便兼容和回退测试

## 项目结构
```text
k12_web/
├── main.py                         # 主入口，默认由 server.app 启动 aiohttp
├── requirements.txt                # Python 依赖
├── README.md                       # 项目说明
├── k12.csv                         # K12 空间 ID 数据源/历史输入
├── team.csv                        # 测试用 AT 数据文件，注意不要外传
├── try_join_first.py               # 早期命令行 request -> accept 申请脚本
├── server/
│   ├── __init__.py
│   ├── app.py                      # aiohttp HTTP/API/WS 服务端和 JSON-RPC 分发
│   ├── k12_service.py              # K12 查询、申请、导出、日志保存
│   ├── jsonrpc.py                  # JSON-RPC 与 event_id 工具
│   ├── gate_client.py              # 旧 Gate WS client，兼容保留
│   ├── storage.py                  # 旧 SQLite 存储，兼容保留
│   └── stream_manager.py           # 旧 Gate stream 管理，兼容保留
├── static/
│   ├── index.html                  # 首页入口
│   ├── websocket.html              # K12 WebSocket 后端版页面
│   ├── websocket.js                # WebSocket 后端版前端逻辑
│   ├── js.html                     # 纯 JS 实验版页面
│   ├── js-only.js                  # 纯 JS 实验版逻辑
│   ├── control.html                # 兼容控制页面
│   ├── control.js                  # 兼容控制页面逻辑
│   ├── app.js                      # 旧入口逻辑，兼容保留
│   └── style.css                   # 页面样式
├── doc/
│   └── API.md                      # API / JSON-RPC 文档
├── db/
│   ├── market.sqlite3              # 旧 SQLite 数据库，兼容保留
│   ├── k12_<account_id>.json       # K12 账号查询报告
│   ├── k12_at_records.jsonl        # AT 查询记录
│   └── k12_operator.log            # 历史遗留操作日志，新版本不再写入
├── log/
│   ├── k12_server.log              # K12 服务运行日志
│   ├── k12_operator.log            # 当前页面操作日志
│   └── server.log                  # 旧日志文件，兼容保留
└── scripts/
    ├── read_btcusdt_1m.py          # 旧 Gate CSV 采集脚本
    └── read_btcusdt_1m_sqlite.py   # 旧 Gate 单 SQLite 采集脚本
```

## 环境要求
- Windows / PowerShell
- Python 3.12，当前使用 `D:\0Code2\py312\python.exe`
- 依赖：`aiohttp`、`requests`
- 可选：Node.js，用于检查前端 JS 语法
- 默认代理：`http://127.0.0.1:7897`

安装依赖：
```powershell
cd D:\PycharmProjects\k12_web
& 'D:\0Code2\py312\python.exe' -m pip install -r requirements.txt
```

## 运行方式
默认运行：
```powershell
cd D:\PycharmProjects\k12_web
& 'D:\0Code2\py312\python.exe' main.py --host 127.0.0.1 --port 8088
```

默认参数：
```text
--host 0.0.0.0
--port 8088
--db db/market.sqlite3
--db-dir db
--log-dir log
--k12-base-url https://chatgpt.com
--k12-proxy http://127.0.0.1:7897
```

指定代理：
```powershell
& 'D:\0Code2\py312\python.exe' main.py --host 127.0.0.1 --port 8088 --k12-proxy http://127.0.0.1:7897
```

不使用代理：
```powershell
& 'D:\0Code2\py312\python.exe' main.py --host 127.0.0.1 --port 8088 --k12-proxy ''
```

启动旧 Gate 行情客户端：
```powershell
& 'D:\0Code2\py312\python.exe' main.py --host 127.0.0.1 --port 8088 --start-gate
```

## 页面入口
首页入口：
```text
http://127.0.0.1:8088/
```

K12 WebSocket 后端版：
```text
http://127.0.0.1:8088/html/websocket
```

纯 JS 实验版：
```text
http://127.0.0.1:8088/html/js
```

兼容控制页：
```text
http://127.0.0.1:8088/control
```

局域网访问时，将 `127.0.0.1` 替换为服务器 IP。

## 命令行参数
```powershell
& 'D:\0Code2\py312\python.exe' main.py [--host HOST] [--port PORT] [--db DB_PATH] [--db-dir DB_DIR] [--log-dir LOG_DIR] [--k12-base-url URL] [--k12-proxy PROXY] [--start-gate]
```

参数说明：
- `--host`：监听地址，默认 `0.0.0.0`
- `--port`：监听端口，默认 `8088`
- `--db`：SQLite 数据库路径，默认 `db/market.sqlite3`
- `--db-dir`：K12 JSON 报告和 AT 记录目录，默认 `db`
- `--log-dir`：日志目录，默认 `log`
- `--k12-base-url`：K12 请求基础 URL，默认 `https://chatgpt.com`
- `--k12-proxy`：K12 后端请求代理，默认 `http://127.0.0.1:7897`
- `--start-gate`：启动旧 Gate 行情客户端，默认不启动

## WebSocket 版页面说明
WebSocket 版页面路径：
```text
/html/websocket
```

页面分为三块：
- AT 信息：输入 AccessToken，点击“提交查询”
- 账号信息：显示账号摘要、workspace 数量、workspace ID、空间类型和空间名
- 操作日志：显示查询、申请和保存过程

按钮状态：
- “提交查询”：运行中置灰，完成、失败或超时后恢复
- “申请空间”：初始禁用；查询成功后启用；申请中置灰；完成、失败或超时后恢复
- “保存日志”：保持禁用，当前由流程自动保存

空间 ID 默认列表在：
```text
static/websocket.html
```

## 数据流说明
```text
Browser /html/websocket
        │
        ├─ JSON-RPC(k12.inspect_at) ───────┐
        ├─ JSON-RPC(k12.apply_workspaces) ─┤
        │                                  ▼
        └────────────── /ws ───────> aiohttp server
                                           │
                                           ├─ requests + proxy -> /backend-api/me
                                           ├─ requests + proxy -> /backend-api/accounts
                                           ├─ requests + proxy -> /invites/request
                                           ├─ requests + proxy -> /invites/accept
                                           ├─ export JSON -> db/
                                           └─ save log -> log/
```

职责边界：
- `static/websocket.js`：只负责页面交互、按钮状态、日志展示和 JSON-RPC 调用
- `server/app.py`：只负责 HTTP 路由、WebSocket 生命周期和 JSON-RPC 方法分发
- `server/k12_service.py`：负责 AT 解码、账号查询、空间申请、报告导出和日志落盘
- `server/jsonrpc.py`：统一生成 JSON-RPC 响应、错误、通知和 `event_id`
- `db/`：保存结构化查询数据
- `log/`：保存运行日志和操作日志

## JSON-RPC 说明
所有 WebSocket 消息使用 JSON-RPC 2.0。

请求示例：
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

响应示例：
```json
{
  "jsonrpc": "2.0",
  "event_id": "1783005834935-result-000004",
  "id": 1,
  "result": {}
}
```

通知示例：
```json
{
  "jsonrpc": "2.0",
  "event_id": "1783005834935-k12_progress-000004",
  "method": "k12.progress",
  "params": {
    "event_id": "1783005834935-k12_progress-000004",
    "stage": "query_accounts",
    "message": "查询账号空间信息中: /backend-api/accounts"
  }
}
```

`event_id` 格式：
```text
unixtime_ms-事件类型-递增计数
```

用途：
- 复盘事件顺序
- 对齐前端进度
- 对齐服务端日志
- 排查请求、响应和通知是否丢失

## WebSocket JSON-RPC 方法
地址：
```text
/ws
```

### `k12.inspect_at`
提交 AT，后端解码、查询账号接口并导出 JSON。

请求：
```json
{"jsonrpc":"2.0","id":1,"method":"k12.inspect_at","params":{"access_token":"eyJ...","operator_log":"..."}}
```

后端动作：
- 校验 AT 必须以 `eyJ` 开头
- 解码 JWT payload
- 查询 `/backend-api/me`
- 查询 `/backend-api/accounts`
- 提取 `workspace_ids`
- 提取 `workspace_details`
- 导出 `db/k12_<account_id>.json`
- 追加 `db/k12_at_records.jsonl`
- 保存操作日志到 `log/k12_operator.log`

### `k12.apply_workspaces`
提交 AT 和空间 ID 列表，后端按顺序申请空间。

请求：
```json
{"jsonrpc":"2.0","id":2,"method":"k12.apply_workspaces","params":{"access_token":"eyJ...","workspace_ids":["631e1603-06cf-4f0b-b79b-d09fbfcfe98d"],"operator_log":"..."}}
```

返回示例：
```json
{
  "success": true,
  "stopped_by": "first_success",
  "accepted_workspace_id": "a65ebb2e-dd7c-4fdb-9a5d-6ccaf6ad00a3",
  "results": [
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

### `k12.save_log`
保存页面操作日志。

请求：
```json
{"jsonrpc":"2.0","id":3,"method":"k12.save_log","params":{"text":"[2026-07-02 18:48:24]..."}}
```

默认输出：
```text
log/k12_operator.log
```

### `k12.latest`
读取 `db/` 中最新的 K12 查询报告。

请求：
```json
{"jsonrpc":"2.0","id":4,"method":"k12.latest","params":{}}
```

### `server.status`
查询 server 状态。

请求：
```json
{"jsonrpc":"2.0","id":5,"method":"server.status","params":{}}
```

## 后端主动推送事件
### `server.hello`
前端连接 `/ws` 后立即推送：
- streams
- stream_status
- offline
- k12_proxy
- k12_latest
- counts
- latest
- recent

### `k12.progress`
查询 AT 时推送阶段进度，例如：
- 收到 AT，开始处理
- 解析 AT 中
- 得到 AT 账号信息
- 查询账号基础信息中
- 得到接口信息
- 查询账号空间信息中
- 导出查询 JSON 中
- 查询流程完成

### `k12.report`
查询完成后推送账号报告。

### `k12.apply_progress`
申请空间时推送阶段进度，例如：
- 开始申请空间，共 N 个
- 申请 xxxx...xxx request中
- 申请 xxxx...xxx request成功
- 申请 xxxx...xxx accept中
- 申请 xxxx...xxx 成功，停止后续申请
- 刷新账号信息中
- 申请空间流程完成

### `k12.log`
保存操作日志后推送保存结果。

## HTTP API
### `GET /`
返回首页入口页面。

### `GET /html/websocket`
返回 K12 WebSocket 后端版页面。

### `GET /html/js`
返回纯 JS 实验版页面。

### `GET /control`
返回兼容控制页面。

### `GET /api/status`
返回服务状态、代理配置、最新 K12 报告摘要和旧 stream 状态。

### `GET /api/kline/recent?contract=BTC_USDT&interval=15m&limit=60`
旧 Gate 兼容 API，返回近期已落地 K 线。

### `GET /api/kline/latest?contract=BTC_USDT&interval=15m`
旧 Gate 兼容 API，返回最新未落地 K 线。

详细说明见：
```text
doc/API.md
```

## 空间申请流程
前端一次传入完整空间 ID 列表，后端按顺序逐个处理。

每个 workspace ID 的请求顺序：
```text
POST /backend-api/accounts/{workspace_id}/invites/request
等待 1.5s
POST /backend-api/accounts/{workspace_id}/invites/accept
```

停止规则：
- `accept_ok=true` 时停止后续申请
- 如果全部失败，返回 `stopped_by=exhausted`
- 如果成功一个，返回 `stopped_by=first_success`

申请成功后：
- 等待 2 秒
- 重新查询 `/backend-api/me`
- 重新查询 `/backend-api/accounts`
- 重新导出账号 JSON
- 保存最终操作日志

## 日志文案
查询成功后，页面操作日志包含：
```text
[YYYY-MM-DD HH:mm:ss]查询eyJ******xxxxx的账号邮箱为xxx@xxx
[YYYY-MM-DD HH:mm:ss]xxx@xxx邮箱当前工作区为[id1, id2]
[YYYY-MM-DD HH:mm:ss]其中id1空间的类型为:workspace，空间名为xxx，请检查邮箱或者刷新主页查看该空间
[YYYY-MM-DD HH:mm:ss]其中id2为个人空间
```

申请空间时，页面操作日志追加：
```text
[YYYY-MM-DD HH:mm:ss]开始申请空间，共11个
[YYYY-MM-DD HH:mm:ss]申请631e...98d request中
[YYYY-MM-DD HH:mm:ss]申请631e...98d request失败
[YYYY-MM-DD HH:mm:ss]申请a65e...0a3 request成功
[YYYY-MM-DD HH:mm:ss]申请a65e...0a3成功，停止后续申请
```

## 数据文件
K12 查询报告目录：
```text
db/
```

### `k12_<account_id>.json`
记录一次账号查询报告。

核心字段：
- `generated_at`
- `access_token_sha256`
- `access_token_preview`
- `decoded`
- `query`
- `workspace_ids`
- `workspace_details`
- `workspace_count`
- `report_path`

### `k12_at_records.jsonl`
记录 AT 查询摘要。

核心字段：
- `recorded_at`
- `access_token_sha256`
- `access_token_preview`
- `account_id`
- `email`
- `phone`
- `plan_type`
- `report_path`

### `market.sqlite3`
旧 Gate 兼容 SQLite 数据库，当前 K12 主流程不依赖它。

## 日志说明
日志目录：
```text
log/
```

当前操作日志：
```text
log/k12_operator.log
```

当前服务日志：
```text
log/k12_server.log
```

日志内容包括：
- server 启动和关闭
- 前端 WS 连接/断开
- JSON-RPC 广播事件
- AT 查询阶段
- 空间申请阶段
- 报告导出路径
- 操作日志保存路径
- 错误堆栈

历史说明：
- `db/k12_operator.log` 是早期版本的操作日志输出位置
- 当前版本 `26.7.2A` 起，操作日志写入 `log/k12_operator.log`

## 早期脚本和兼容模块说明
项目保留早期 K12 命令行脚本：

### K12 快速加入脚本
```powershell
& 'D:\0Code2\py312\python.exe' try_join_first.py --at "eyJ..."
```

说明：
- 读取 `k12.csv`
- 按 `request -> accept` 流程尝试加入空间
- 成功一个即停止
- 当前推荐使用 WebSocket 页面，不再优先使用该脚本

项目也保留旧 Gate 测试脚本：

### CSV 采集脚本
```powershell
& 'D:\0Code2\py312\python.exe' scripts\read_btcusdt_1m.py
```

### 单 SQLite 采集脚本
```powershell
& 'D:\0Code2\py312\python.exe' scripts\read_btcusdt_1m_sqlite.py
```

说明：这两个脚本主要用于旧 Gate K 线推送、`window_closed` 和 SQLite 结构验证，当前 K12 工作台主流程不依赖它们。

## 运行验证记录
已完成以下验证：
- 验证 `GET /` 返回首页入口
- 验证 `GET /html/websocket` 返回 K12 WebSocket 后端版页面
- 验证 `GET /html/js` 返回纯 JS 实验版页面
- 验证 `/ws` 连接后收到 `server.hello`
- 验证 `k12.inspect_at` 使用 JSON-RPC 2.0 请求和响应
- 验证 `k12.inspect_at` 可成功查询 `/backend-api/me`
- 验证 `k12.inspect_at` 可成功查询 `/backend-api/accounts`
- 验证账号信息框显示 workspace 数量和空间详情
- 验证查询报告导出到 `db/k12_<account_id>.json`
- 验证报告中包含 `workspace_ids` 和 `workspace_details`
- 验证操作日志保存到 `log/k12_operator.log`
- 验证 `k12.apply_workspaces` 使用 JSON-RPC 2.0 请求和响应
- 验证申请空间按 workspace ID 顺序逐个处理
- 验证首个 accept 成功后停止后续申请
- 验证申请进度通过 `k12.apply_progress` 推送
- 验证前端“提交查询”运行中置灰，完成后恢复
- 验证前端“申请空间”查询成功后启用，申请中置灰，完成后恢复
- 验证 `node --check static/websocket.js` 通过
- 验证 `python -m compileall server main.py` 通过
- 验证文档中的操作日志路径已从 `db/` 调整为 `log/`

## 版本
当前版本：`26.7.2A`
最后更新：`2026-07-02`

## 更新日志
### 26.7.2A (2026-07-02)
- 新增：K12 空间申请 Web 工作台主题
- 新增：首页 `/`，提供 WebSocket 后端版和纯 JS 实验版入口
- 新增：WebSocket 后端版页面 `/html/websocket`
- 新增：纯 JS 实验版页面 `/html/js`
- 新增：AT 输入框、账号信息框、空间 ID 输入框和操作日志框
- 新增：`k12.inspect_at` JSON-RPC 方法
- 新增：AT JWT payload 本地解码逻辑
- 新增：后端代理查询 `/backend-api/me`
- 新增：后端代理查询 `/backend-api/accounts`
- 新增：账号查询阶段 `k12.progress` 通知
- 新增：账号报告 `k12.report` 通知
- 新增：账号 JSON 报告导出到 `db/k12_<account_id>.json`
- 新增：AT 查询记录追加到 `db/k12_at_records.jsonl`
- 新增：workspace ID 提取
- 新增：workspace 详情提取，包括空间类型、空间名和角色
- 新增：账号信息框显示 workspace 数量、ID 和详情
- 新增：查询日志脱敏 AccessToken，格式为 `eyJ******xxxxx`
- 新增：浏览器本地时间日志，精确到秒
- 新增：空间详情日志文案
- 调整：个人空间日志简化为“其中 xxx 为个人空间”
- 新增：`k12.apply_workspaces` JSON-RPC 方法
- 新增：申请空间按钮，查询成功后启用
- 新增：申请空间时前端传入完整空间 ID 列表
- 新增：后端按列表顺序逐个执行 `request -> accept`
- 新增：`request` 成功后等待 `1.5s` 再 `accept`
- 新增：首个 `accept` 成功后停止后续申请
- 新增：申请进度 `k12.apply_progress` 通知
- 新增：申请完成后重新查询账号信息并导出 JSON
- 新增：WebSocket RPC 超时保护
- 调整：WebSocket heartbeat 调整为 `120s`
- 调整：所有新增 WebSocket 请求统一使用 JSON-RPC 2.0
- 调整：后端响应统一使用 `jsonrpc.result()` / `jsonrpc.error()`
- 调整：后端主动推送统一使用 `jsonrpc.notification()`
- 调整：默认代理设置为 `http://127.0.0.1:7897`
- 调整：操作日志从 `db/k12_operator.log` 迁移到 `log/k12_operator.log`
- 调整：服务日志命名为 `log/k12_server.log`
- 保留：旧 Gate 行情模块作为兼容代码，默认不启动
- 文档：按 `2606_GATE` 项目 README 格式重写当前项目文档
