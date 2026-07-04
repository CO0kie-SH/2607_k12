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

function renderAccountInfo(profile) {
  $('account-info').value = [
    `模式: 纯前端本地解析`,
    `邮箱: ${profile.email || '-'}`,
    `手机: ${profile.phone || '-'}`,
    `计划: ${profile.planType || '-'}`,
    `账号 ID: ${profile.accountId || '-'}`,
    `账号用户 ID: ${profile.accountUserId || '-'}`,
    `用户 ID: ${profile.userId || '-'}`,
    `签发时间: ${formatUnixTime(profile.issuedAt)}`,
    `过期时间: ${formatUnixTime(profile.expiresAt)}`,
    `Scopes: ${profile.scopes.length ? profile.scopes.join(', ') : '-'}`,
    `Workspace 数量: 静态页面不查询`,
    `Workspace IDs: 静态页面不查询`,
    `/backend-api/me: 静态页面不请求`,
    `/backend-api/accounts: 静态页面不请求`,
    `说明: 本页只解析 AT 自带 claims，不联网，不判断空间状态`,
  ].join('\n');
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

    appendProgress('静态页面不请求 /backend-api/me');
    appendProgress('静态页面不请求 /backend-api/accounts');
    const finalLog = `${queryLog}\n${logLine(`${label}静态解析完成，未联网查询 workspace`)}`;

    renderAccountInfo(profile);
    $('operator-log').value = finalLog;
    appendProgress('完成');
    setResult('纯前端本地解析完成', 'ok');
  } catch (err) {
    setResult(`解析失败: ${err.message || JSON.stringify(err)}`, 'err');
    appendProgress(`解析失败: ${err.message || JSON.stringify(err)}`);
  } finally {
    $('inspect-btn').disabled = false;
    $('inspect-btn').classList.remove('running');
  }
}

$('inspect-btn').addEventListener('click', submitAt);
