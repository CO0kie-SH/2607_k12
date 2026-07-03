const $ = (id) => document.getElementById(id);

function nextPath() {
  const params = new URLSearchParams(location.search);
  const next = params.get('next') || '/html/websocket';
  return next.startsWith('/') ? next : '/html/websocket';
}

function entryPath(path, entryToken) {
  const url = new URL(path, location.origin);
  url.searchParams.set('entry', entryToken);
  return `${url.pathname}${url.search}${url.hash}`;
}

function initialAuthMessage() {
  const params = new URLSearchParams(location.search);
  if (params.get('session') === 'expired') return '会话已失效，请重新登录';
  return '等待输入账号';
}

function setAuthStatus(text, ok = false) {
  const el = $('auth-status');
  el.textContent = text;
  el.className = `status ${ok ? 'connected' : 'disconnected'}`;
}

function setAuthResult(text, kind = 'idle') {
  const el = $('auth-result');
  el.textContent = text;
  el.className = `result-line ${kind}`;
}

function credentialsPayload() {
  return {
    username: $('login-username').value.trim(),
    password: $('login-password').value,
  };
}

function renderRiskResult(data) {
  const windowSeconds = data.window_seconds || 60;
  $('risk-output').value = [
    `remote: ${data.remote || '-'}`,
    `近${windowSeconds}秒请求次数(RPM): ${data.request_count ?? '-'}`,
    `可用次数: ${data.usable_count ?? '-'}`,
  ].join('\n');
}

async function postJson(path, payload) {
  const response = await fetch(path, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const err = new Error(data.message || `请求失败: ${response.status}`);
    err.data = data;
    throw err;
  }
  return data;
}

async function queryAccount() {
  const payload = credentialsPayload();
  if (!payload.username || !payload.password) {
    setAuthResult('请输入账号名和密码', 'err');
    return;
  }

  const btn = $('query-account-btn');
  btn.disabled = true;
  btn.classList.add('running');
  setAuthResult('查询中...', 'busy');
  try {
    const data = await postJson('/api/auth/query', payload);
    renderRiskResult(data);
    setAuthResult('查询完成', 'ok');
  } catch (err) {
    if (err.data) renderRiskResult(err.data);
    setAuthResult(err.message || '查询失败', 'err');
  } finally {
    btn.disabled = false;
    btn.classList.remove('running');
  }
}

async function login() {
  const payload = credentialsPayload();
  if (!payload.username || !payload.password) {
    setAuthResult('请输入账号名和密码', 'err');
    return;
  }

  const btn = $('login-btn');
  btn.disabled = true;
  btn.classList.add('running');
  setAuthResult('登录中...', 'busy');
  try {
    const data = await postJson('/api/auth/login', payload);
    if (!data.entry_token) throw new Error('登录响应缺少 entry_token，请重启后端服务并确认前后端版本一致');
    renderRiskResult(data);
    setAuthStatus(`已登录: ${data.username}`, true);
    setAuthResult('登录成功', 'ok');
    window.setTimeout(() => {
      location.href = entryPath(nextPath(), data.entry_token);
    }, 250);
  } catch (err) {
    if (err.data) renderRiskResult(err.data);
    setAuthStatus('未登录', false);
    setAuthResult(err.message || '登录失败', 'err');
  } finally {
    btn.disabled = false;
    btn.classList.remove('running');
  }
}

async function resetAuthState() {
  setAuthStatus('未登录', false);
  setAuthResult(initialAuthMessage(), 'idle');
  try {
    await fetch('/api/auth/logout', {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
    });
  } catch {
    setAuthStatus('未登录', false);
  }
}

$('query-account-btn').addEventListener('click', queryAccount);
$('login-btn').addEventListener('click', login);
$('login-password').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') login();
});

resetAuthState();
