const $ = (id) => document.getElementById(id);

$('account-info').value = '';

function setResult(text, kind = 'idle') {
  const el = $('result-line');
  el.textContent = text;
  el.className = `result-line ${kind}`;
}

function browserTime() {
  const d = new Date();
  const pad = (v) => String(v).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function logLine(text) {
  return `[${browserTime()}]${text}`;
}

function tokenLabel(accessToken) {
  return `${accessToken.slice(0, 3)}******${accessToken.slice(-5)}`;
}

function appendProgress(message) {
  const line = `[${new Date().toLocaleTimeString()}] ${message}`;
  const el = $('progress-log');
  el.textContent = el.textContent && el.textContent !== '等待提交' ? `${el.textContent}\n${line}` : line;
  el.scrollTop = el.scrollHeight;
}

function decodeJwt(accessToken) {
  const payload = accessToken.split('.')[1];
  if (!payload) throw new Error('AT 不是有效 JWT');
  const normalized = payload.replace(/-/g, '+').replace(/_/g, '/');
  const padded = normalized + '='.repeat((4 - normalized.length % 4) % 4);
  return JSON.parse(decodeURIComponent(escape(atob(padded))));
}

function profileFromClaims(claims) {
  const profile = claims['https://api.openai.com/profile'] || {};
  const auth = claims['https://api.openai.com/auth'] || {};
  return {
    email: profile.email || '',
    phone: profile.phone_number || '',
    planType: auth.chatgpt_plan_type || '',
    accountId: auth.chatgpt_account_id || '',
    accountUserId: auth.chatgpt_account_user_id || '',
    userId: auth.chatgpt_user_id || auth.user_id || '',
    issuedAt: claims.iat || '',
    expiresAt: claims.exp || '',
    scopes: claims.scp || [],
  };
}

function accountLabel(profile) {
  return profile.email || profile.phone || profile.accountId || '未知账号';
}

function formatUnixTime(sec) {
  return sec ? new Date(sec * 1000).toLocaleString() : '-';
}

function endpointLine(path, result) {
  if (!result) return `${path}: 未查询`;
  if (result.error) return `${path}: ${result.error}`;
  const suffix = `status=${result.status} content-type=${result.contentType || '-'}`;
  if (result.jsonError) return `${path}: ${suffix} json_error=${result.jsonError}`;
  return `${path}: ${suffix}`;
}

function extractWorkspaceIds(accountsResult) {
  const ids = new Set();
  const data = accountsResult?.data;

  function walk(value, depth = 0) {
    if (depth > 6 || value == null) return;
    if (Array.isArray(value)) {
      value.forEach(item => walk(item, depth + 1));
      return;
    }
    if (typeof value === 'object') {
      Object.entries(value).forEach(([key, item]) => {
        if (key === 'id' && typeof item === 'string' && item.length === 36 && item.split('-').length === 5) {
          ids.add(item);
        }
        walk(item, depth + 1);
      });
    }
  }

  walk(data);
  return Array.from(ids).sort();
}

function renderAccountInfo(profile, query = {}) {
  const workspaceIds = extractWorkspaceIds(query.accounts);
  $('account-info').value = [
    `模式: 纯前端浏览器查询`,
    `邮箱: ${profile.email || '-'}`,
    `手机: ${profile.phone || '-'}`,
    `计划: ${profile.planType || '-'}`,
    `账号 ID: ${profile.accountId || '-'}`,
    `账号用户 ID: ${profile.accountUserId || '-'}`,
    `用户 ID: ${profile.userId || '-'}`,
    `签发时间: ${formatUnixTime(profile.issuedAt)}`,
    `过期时间: ${formatUnixTime(profile.expiresAt)}`,
    `Scopes: ${profile.scopes.length ? profile.scopes.join(', ') : '-'}`,
    `Workspace 数量: ${workspaceIds.length}`,
    `Workspace IDs: ${workspaceIds.length ? workspaceIds.join(', ') : '-'}`,
    endpointLine('/backend-api/me', query.me),
    endpointLine('/backend-api/accounts', query.accounts),
  ].join('\n');
}

async function queryEndpoint(accessToken, path) {
  try {
    const response = await fetch(`https://chatgpt.com${path}`, {
      method: 'GET',
      mode: 'cors',
      cache: 'no-store',
      headers: {
        accept: '*/*',
        authorization: `Bearer ${accessToken}`,
      },
    });
    const text = await response.text();
    let data = null;
    let jsonError = '';
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (err) {
        jsonError = err.message || String(err);
      }
    }
    return {
      status: response.status,
      ok: response.status === 200 && !jsonError,
      contentType: response.headers.get('content-type') || '',
      textLength: text.length,
      textPreview: text.slice(0, 500),
      jsonError,
      data,
    };
  } catch (err) {
    return {
      ok: false,
      error: `${err.name || 'Error'}: ${err.message || String(err)}`,
    };
  }
}

async function submitAt() {
  const accessToken = $('access-token').value.trim();
  if (!accessToken.startsWith('eyJ')) {
    setResult('AT 必须以 eyJ 开头', 'err');
    return;
  }

  $('inspect-btn').disabled = true;
  $('inspect-btn').classList.add('running');
  $('progress-log').textContent = '';
  setResult('本地解析中...', 'busy');

  try {
    appendProgress('本地解析 AT');
    const claims = decodeJwt(accessToken);
    const profile = profileFromClaims(claims);
    const label = accountLabel(profile);
    const email = profile.email || '-';
    const queryLog = logLine(`查询${tokenLabel(accessToken)}的账号邮箱为${email}`);

    appendProgress('查询账号基础信息中: /backend-api/me');
    const me = await queryEndpoint(accessToken, '/backend-api/me');
    appendProgress(me.ok ? '得到接口信息: /backend-api/me' : `接口查询失败: /backend-api/me ${me.error || me.status}`);

    appendProgress('查询账号空间信息中: /backend-api/accounts');
    const accounts = await queryEndpoint(accessToken, '/backend-api/accounts');
    appendProgress(accounts.ok ? '得到接口信息: /backend-api/accounts' : `接口查询失败: /backend-api/accounts ${accounts.error || accounts.status}`);

    const query = { me, accounts };
    const workspaceIds = extractWorkspaceIds(accounts);
    const finalLog = `${queryLog}\n${logLine(`${label}邮箱当前工作区为[${workspaceIds.length ? workspaceIds.join(', ') : '纯前端查询失败或无工作区'}]`)}`;

    renderAccountInfo(profile, query);
    $('operator-log').value = finalLog;
    appendProgress('完成');
    setResult(me.ok && accounts.ok ? '纯前端查询完成' : '纯前端查询完成，但存在接口失败', me.ok && accounts.ok ? 'ok' : 'err');
  } catch (err) {
    setResult(`解析失败: ${err.message || JSON.stringify(err)}`, 'err');
    appendProgress(`解析失败: ${err.message || JSON.stringify(err)}`);
  } finally {
    $('inspect-btn').disabled = false;
    $('inspect-btn').classList.remove('running');
  }
}

$('inspect-btn').addEventListener('click', submitAt);
