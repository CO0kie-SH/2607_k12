# AT 自带信息与 Workspace 接口参考

最后更新：`2026-07-05`

本文面向其它项目复用，记录本项目当前验证过的 Access Token（AT）离线解析字段、账号空间查询接口、空间申请接口和 CSV 配置格式。

说明：这里记录的是当前项目的实际实现和已观察到的返回结构，不等同于稳定公开 API 合约。其它项目接入时应保留错误处理、重试和字段缺失兼容。

## 1. AT 自带信息

当前可解析的 AT 是 JWT 格式：

```text
header.payload.signature
```

`payload` 是 Base64URL 编码的 JSON，不是加密内容。因此只要拿到 AT 字符串，就可以离线解析 payload 中已经写入的 claims。这个过程不需要联网。

离线解析只能读取令牌自带信息，不能证明令牌有效、未过期、未篡改，也不能得到实时 workspace 状态。实时账号和空间信息必须调用后端接口查询。

## 2. 本项目字段映射

当前 `server/k12_service.py::decode_access_token()` 提取这些字段：

| 项目字段 | JWT claim 路径 | 含义 | 是否实时 |
| --- | --- | --- | --- |
| `email` | `https://api.openai.com/profile.email` | AT payload 中的邮箱 | 否 |
| `phone` | `https://api.openai.com/profile.phone_number` | AT payload 中的手机号 | 否 |
| `plan_type` | `https://api.openai.com/auth.chatgpt_plan_type` | AT payload 中的计划类型 | 否 |
| `account_id` | `https://api.openai.com/auth.chatgpt_account_id` | 当前账号 ID，本项目也用于报告文件名 | 否 |
| `account_user_id` | `https://api.openai.com/auth.chatgpt_account_user_id` | 账号内用户 ID | 否 |
| `user_id` | `https://api.openai.com/auth.chatgpt_user_id` 或 `https://api.openai.com/auth.user_id` | 用户 ID | 否 |
| `client_id` | `client_id` | 客户端 ID | 否 |
| `issuer` | `iss` | 签发方 | 否 |
| `issued_at` | `iat` | 签发时间，通常是 Unix 秒 | 否 |
| `expires_at` | `exp` | 过期时间，通常是 Unix 秒 | 否 |
| `scopes` | `scp` | scope 列表 | 否 |
| `raw_claims` | 完整 payload | 原始 claims | 否 |

注意：表里的 `https://api.openai.com/profile.email` 是便于阅读的缩写。实际 JSON 通常是 namespace 对象：

```json
{
  "https://api.openai.com/profile": {
    "email": "name@example.com",
    "phone_number": ""
  },
  "https://api.openai.com/auth": {
    "chatgpt_plan_type": "plus",
    "chatgpt_account_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "chatgpt_account_user_id": "...",
    "chatgpt_user_id": "..."
  }
}
```

## 3. 离线解析示例

Python 只读 payload 示例：

```python
import base64
import json


def decode_at_payload(access_token: str) -> dict:
    parts = access_token.split(".")
    if len(parts) < 2:
        raise ValueError("access token is not a JWT")
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))


claims = decode_at_payload("eyJ...")
profile = claims.get("https://api.openai.com/profile", {})
auth = claims.get("https://api.openai.com/auth", {})
print(profile.get("email"))
print(auth.get("chatgpt_account_id"))
```

浏览器 JS 只读 payload 示例：

```js
function decodeAtPayload(accessToken) {
  const parts = accessToken.split('.');
  if (parts.length < 2) throw new Error('access token is not a JWT');
  const normalized = parts[1].replace(/-/g, '+').replace(/_/g, '/');
  const padded = normalized.padEnd(normalized.length + ((4 - normalized.length % 4) % 4), '=');
  const jsonText = decodeURIComponent(
    Array.from(atob(padded), ch => `%${ch.charCodeAt(0).toString(16).padStart(2, '0')}`).join('')
  );
  return JSON.parse(jsonText);
}
```

## 4. 后端请求通用配置

当前主流程使用 `curl_cffi`，默认模拟 Chrome：

```text
transport: curl_cffi
impersonate: chrome
base_url: https://chatgpt.com
proxy: 可选；none/direct/off/0/false/null/空字符串表示直连
GET timeout: 默认 15 秒
POST timeout: 3 秒
```

通用 headers：

```http
accept: */*
authorization: Bearer <AT>
content-type: application/json
oai-device-id: <uuid4>
user-agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/135.0.0.0 Safari/537.36
```

建议由后端代发这些请求，不建议让静态前端页面直接请求。原因是浏览器会受到 CORS、登录态、网络环境和 Token 泄露面影响。

## 5. 查询账号与空间

### `GET /backend-api/me`

用途：查询当前 AT 对应账号的基础信息。

请求：

```http
GET https://chatgpt.com/backend-api/me
authorization: Bearer <AT>
```

当前项目只保存完整 JSON，并在报告中记录请求状态；展示层主要依赖 AT payload 和 `/backend-api/accounts`。

### `GET /backend-api/accounts`

用途：查询当前 AT 可见的账号空间列表。

请求：

```http
GET https://chatgpt.com/backend-api/accounts
authorization: Bearer <AT>
```

当前项目的提取规则：

- `workspace_ids`：深度遍历返回 JSON，收集所有 key 为 `id`、长度为 36、包含 4 个 `-` 的字符串。
- `workspace_details`：优先读取 `data.items`，每个 item 提取：
  - `id`
  - `type`: `structure` 或 `type`
  - `name`
  - `role`: `current_user_role`

已观察到的 workspace 相关字段包括：

```text
id
name
structure
processor
current_user_role
eligible_for_auto_reactivation
created_time
```

字段边界：

- `created_time` 如存在，表示接口返回的创建时间线索，不是从 UUID 本身可靠推导出来的。
- 当前不能仅凭 `/backend-api/accounts` 稳定判断空间是否付费、是否 active、是否停用。
- 当前未稳定观察到 `status`、`disabled`、`suspended`、`active`、`subscription_status`、`billing_status`、`plan_status`、`deactivated_at`、`cancel_at` 等直接状态字段。
- 本项目前端会对返回内容中的 `deactivated_workspace` 字符串做风险提示，但这只是前端校验，不是可靠状态接口。

## 6. 申请空间接口

空间申请按顺序调用两个接口：

```http
POST https://chatgpt.com/backend-api/accounts/{workspace_id}/invites/request
authorization: Bearer <AT>
content-type: application/json
```

```http
POST https://chatgpt.com/backend-api/accounts/{workspace_id}/invites/accept
authorization: Bearer <AT>
content-type: application/json
```

请求体：

```text
空 body
```

当前项目判定：

- HTTP `2xx` 视为成功。
- POST 不强制解析 JSON，避免 `204 No Content` 被误判失败。
- `request` 成功后等待 `1.5` 秒再调用 `accept`。
- `accept` 成功后等待 `2` 秒。
- 默认继续尝试后续 workspace。
- 只有调用方显式设置 `stop_on_success=true` 时，首个成功后停止。

推荐流程：

```text
1. 离线解析 AT，得到 email/account_id 等基础信息。
2. GET /backend-api/me。
3. GET /backend-api/accounts，得到当前 workspace 列表。
4. 根据 CSV 候选列表过滤邮箱后缀、available、已存在空间、账号自身 ID。
5. 对每个候选 workspace 调用 invites/request。
6. request 成功后等待 1.5 秒，调用 invites/accept。
7. 全部尝试完成后刷新 /backend-api/accounts。
8. 如果目标 workspace 未立即出现，间隔 1 秒重试，最多 3 次。
9. 如果接口表面失败但刷新后出现新增 workspace，应按“刷新后确认加入”处理。
```

## 7. 申请后确认

申请接口返回失败不一定代表最终没有加入空间，因此当前项目会在申请后重新查询账号空间。

确认规则：

```text
final_workspace_ids = GET /backend-api/accounts 后提取出的 workspace_ids
expected_workspace_id in final_workspace_ids => 视为已加入
```

重试规则：

```text
最多 3 次
间隔 1 秒
只对 /backend-api/accounts 重试
```

前端还会对比申请前后的 workspace 列表。如果刷新报告中出现新增空间，即使前面的申请接口显示失败，也会用新增空间结果覆盖纯失败提示。

## 8. Workspace CSV 格式

当前项目根目录 `k12.csv` 行格式：

```csv
workspace_id,plan_type,email_suffix,available
ff598c4d-ccaf-40c1-bfaa-cb94565764b1,k12,gmail.com,true
1e595494-2426-4946-b688-58ba75604bcc,k12,*,true
```

字段说明：

| 列 | 名称 | 说明 |
| --- | --- | --- |
| 1 | `workspace_id` | 目标 workspace UUID |
| 2 | `plan_type` | 业务分类，当前常见为 `k12` |
| 3 | `email_suffix` | 允许申请的邮箱后缀；空值或 `*` 表示任意后缀 |
| 4 | `available` | 只有 `true` 才参与申请 |

兼容规则：

- 可以带 `workspace_id,...` 表头；当前项目启动缓存时会自动忽略表头。
- 后端申请时只取第一列 UUID，后面的 CSV 字段不会传给官方接口。
- 候选列表会去重并跳过空行。

建议其它项目把这些前端校验也放到后端兜底：

- 当前账号已存在的 workspace ID 不再申请。
- 当前 AT 的 `account_id` 出现在候选空间列表时，不按 workspace 申请。
- 返回内容出现 `deactivated_workspace` 时，提示用户不要使用停用空间继续申请。
- 邮箱后缀不匹配时不申请，第三列为空或 `*` 时按任意后缀处理。

## 9. 本项目响应封装

HTTP client 返回统一结构：

```json
{
  "status": 200,
  "ok": true,
  "content_type": "application/json",
  "text_length": 1234,
  "text_preview": "...",
  "json_error": "",
  "data": {},
  "proxy_used": "",
  "transport": "curl_cffi",
  "impersonate": "chrome"
}
```

请求异常时：

```json
{
  "ok": false,
  "error": "Exception(...)",
  "proxy_used": "",
  "transport": "curl_cffi"
}
```

`ok` 的含义：

- GET：HTTP `2xx` 且 JSON 解析成功。
- POST：HTTP `2xx` 即可，不要求 JSON。

## 10. 安全和隐私注意

其它项目复用时建议保留这些约束：

- 不在前端显示完整 AT、完整 headers、本地日志路径和报告路径。
- 日志中只保存 AT hash 或短 preview，不保存完整 AT。
- RT 不进入本项目主流程，也不要落盘保存。
- 离线 claims 只用于展示和预校验，不能当作权限判断依据。
- 真正权限判断以后端接口返回为准。
- 公开部署时应启用登录、Origin 白名单、会话超时和后端限流。
- 后端应避免把 `Authorization` header 返回给前端或浏览器网络响应。

