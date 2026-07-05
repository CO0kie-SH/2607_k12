# AT / RT 与纯 JS 页面说明

最后更新：`2026-07-04`

本文记录本项目中 Access Token（AT）、Refresh Token（RT）和纯 JS 页面 `/html/js` 的关系。

## 纯 JS 页面能做什么

`/html/js` 当前是浏览器前端页面，不经过本项目后端代理，也不发起对外网络请求。

它当前只保留一类能力：

1. 本地解析 AT。

本地解析 AT 不需要联网，因为当前使用的 AT 是 JWT 格式，JWT 的 payload 是 Base64URL 编码的 JSON。浏览器只要把第二段 payload 解码，就能看到其中已经写入的部分账号信息。

旧版纯 JS 页面曾尝试让浏览器直接请求官方接口，但该路径会受到同源/CORS、浏览器登录态和网络环境影响。当前封版已移除这类外部请求，避免静态页面因跨域问题产生误判。

## 为什么不联网也能看到一部分信息

JWT 常见结构是三段：

```text
header.payload.signature
```

三段含义：

```text
header     令牌类型、签名算法等元数据
payload    声明信息，也叫 claims
signature  签名，用于验证 header 和 payload 未被篡改
```

`payload` 只是 Base64URL 编码，不是加密。只要拿到 JWT 字符串，就能离线解码 payload。

本项目从 AT payload 中读取这些字段：

```text
https://api.openai.com/profile.email
https://api.openai.com/profile.phone_number
https://api.openai.com/auth.chatgpt_plan_type
https://api.openai.com/auth.chatgpt_account_id
https://api.openai.com/auth.chatgpt_account_user_id
https://api.openai.com/auth.chatgpt_user_id
iat
exp
scp
```

这里有两个边界：

- “能解码”来自 JWT 标准格式。
- “能看到哪些字段”由签发方写入 payload 决定，不是 JWT 标准强制要求。

所以，纯 JS 离线解析只能看到 AT 自带的 claims。workspace 列表、账号最新状态、权限变化等实时信息，不能由静态页面直接得出；需要走 WebSocket 后端版，由后端调用官方接口查询。

## Workspace 状态判断边界

当前后端查询 `/backend-api/accounts` 时，已观察到的空间字段主要包括：

```text
id
name
structure
processor
current_user_role
eligible_for_auto_reactivation
created_time
```

当前返回中没有稳定出现这些直接状态字段：

```text
status
disabled
suspended
active
subscription_status
billing_status
plan_status
deactivated_at
cancel_at
```

因此，本项目目前只能稳定展示：

- 当前 AT 可见的 workspace 列表。
- workspace 的结构类型、处理器、当前用户角色和创建时间等字段。
- `processor=stripe` 这类字段可以作为“订阅/付费处理器相关空间”的线索，但不能证明当前账号本人正在付费，也不能证明空间当前处于 active 状态。
- `eligible_for_auto_reactivation=true` 只能按字段名理解为“可能具备自动重新激活资格”，不能直接等同于“已停用”或“可用”。

结论：目前没有可靠字段可以直接判断哪个 workspace 已停用。后续如果要做停用/付费状态判断，需要找到更明确的官方状态接口或在返回数据中观察到稳定的状态、订阅、账单字段后再接入。

## JWT 解码不等于验证

离线解码只是读取 payload：

```text
Base64URL decode(payload)
JSON.parse(payload)
```

这不能证明令牌是真的、没过期、没被篡改。

严格验证还需要：

```text
1. 校验 signature
2. 校验 iss / aud / azp 等签发与受众字段
3. 校验 exp / nbf / iat 等时间字段
4. 校验 scope / 权限字段
5. 必要时向服务端或授权服务器确认令牌状态
```

本项目纯 JS 页面只做展示和辅助判断，不把离线解析结果当作安全决策依据。真正查询账号和申请空间时，以 WebSocket 后端版为准，后端会把 AT 作为 Bearer Token 发给目标接口，让目标接口做权限判断。

## AT 是什么

AT 是 Access Token，通常用于访问资源接口。

典型用途：

```text
Authorization: Bearer <access_token>
```

特点：

- 通常有效期较短。
- 可以是 JWT，也可以是不透明字符串。
- 用于访问 API，例如查询当前用户、查询账号、查询 workspace。
- 泄露后，在过期前可能被直接拿来调用接口。

在本项目中，AT 用于：

```text
/backend-api/me
/backend-api/accounts
/backend-api/accounts/{workspace_id}/invites/request
/backend-api/accounts/{workspace_id}/invites/accept
```

## RT 是什么

RT 是 Refresh Token，通常用于向授权服务器换取新的 AT。

典型用途：

```text
POST /oauth/token
grant_type=refresh_token
refresh_token=<refresh_token>
```

特点：

- 通常有效期比 AT 长。
- 一般不发给资源 API。
- 只发给授权服务器，用于刷新或续期。
- 泄露风险通常比 AT 更高，因为它可能持续换取新的 AT。
- 很多系统会做 RT 轮换：每次刷新都会返回新的 RT，旧 RT 失效。

本项目当前主流程不使用 RT，也不保存 RT。

## AT 与 RT 的关系

常见关系如下：

```text
用户登录
  -> 授权服务器签发 AT 和 RT

客户端访问资源接口
  -> 使用 AT

AT 过期
  -> 客户端使用 RT 向授权服务器换新 AT

RT 过期、撤销或轮换失败
  -> 需要用户重新登录
```

可以理解为：

```text
AT = 进接口的短期通行证
RT = 换新通行证的长期凭据
```

## 常用方法

### 1. 本地查看 AT payload

适用场景：快速查看邮箱、账号 ID、过期时间、scope。

注意：只能查看，不能证明有效。

### 2. 检查 AT 是否过期

读取 `exp`：

```text
exp < 当前 Unix 时间戳
```

如果过期，资源接口通常会返回 `401` 或类似错误。

### 3. 使用 AT 调用资源接口

```text
Authorization: Bearer <AT>
```

适用场景：查询当前用户、账号、workspace 等实时信息。

本项目当前不在纯 JS 页面中执行这一步，避免浏览器跨域问题；主流程由 WebSocket 后端版完成官方接口查询。

### 4. 使用 RT 刷新 AT

```text
grant_type=refresh_token
refresh_token=<RT>
```

适用场景：AT 过期但会话仍可续期。

注意：RT 应只在可信后端或安全存储中处理，不建议暴露在普通前端页面或日志中。

### 5. 撤销或丢弃令牌

常见做法：

```text
客户端删除本地 token
服务端删除 session
授权服务器撤销 refresh token
```

只删除本地 AT 不一定能让已签发 AT 立即失效，具体取决于签发方是否支持服务端撤销或短 TTL。

## 本项目中的安全约束

- AT 输入框里的完整 AT 不写入后端日志。
- 查询报告只保存 `access_token_sha256` 和 `access_token_preview`。
- `operator_log` 中只记录脱敏后的 AT。
- 不保存 RT。
- 纯 JS 页面只适合离线解析和辅助观察，不请求外部接口，不建议作为公网主入口。

## 结论

“不联网也能看到部分账号信息”不是因为项目破解了什么，而是因为 AT 使用了 JWT 这种可公开解码 payload 的标准格式。

JWT 让客户端可以离线读取 claims，但这些 claims 是否存在、字段名是什么、能不能用于权限判断，都由签发方和服务端校验逻辑决定。

本项目主流程仍以 WebSocket 后端版为准：离线解析只做展示，真正账号列表和空间申请以接口返回为准。当前接口返回字段不足以可靠判断 workspace 是否停用，不能把 `processor` 或 `eligible_for_auto_reactivation` 当作最终状态结论。
