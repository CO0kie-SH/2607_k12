const $ = (id) => document.getElementById(id);
const state = { entryToken: '', enterTimer: null };
const WORKSPACE_CSV_STORAGE_KEY = 'k12_workspace_csv';

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

function setEnterButton(enabled, text = '进入工作台') {
  const btn = $('enter-workspace-btn');
  btn.disabled = !enabled;
  btn.textContent = text;
  btn.classList.toggle('inactive-btn', !enabled);
}

function clearEntryToken() {
  state.entryToken = '';
  if (state.enterTimer) {
    window.clearTimeout(state.enterTimer);
    state.enterTimer = null;
  }
  clearWorkspaceCsv();
  setEnterButton(false);
}

function saveWorkspaceCsv(value) {
  try {
    sessionStorage.setItem(WORKSPACE_CSV_STORAGE_KEY, value);
  } catch {
    // Session storage can be unavailable in hardened browser modes.
  }
}

function clearWorkspaceCsv() {
  try {
    sessionStorage.removeItem(WORKSPACE_CSV_STORAGE_KEY);
  } catch {
    // Ignore storage failures; the WebSocket page will keep an empty list.
  }
}

function credentialsPayload() {
  return {
    username: $('login-username').value.trim(),
    password: $('login-password').value,
  };
}

function renderRiskResult(data) {
  const windowSeconds = data.window_seconds || 60;
  const lines = [
    `账号: ${data.username || data.session_mark || '-'}`,
    `remote: ${data.remote || '-'}`,
    `近${windowSeconds}秒请求次数(RPM): ${data.request_count ?? '-'}`,
    `当前账号连接会话数: ${data.websocket_session_count ?? '-'}`,
    `当前账号登录会话数: ${data.active_session_count ?? '-'}`,
    `可用次数: ${data.usable_count ?? '-'}`,
  ];
  if (data.named_whitelist) lines.push(`白名单: ${data.session_mark || data.username || '命名白名单'}`);
  if (data.local_whitelist) lines.push('白名单: 127.0.0.1');
  $('risk-output').value = lines.join('\n');
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
  const btn = $('query-account-btn');
  clearEntryToken();
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
  const btn = $('login-btn');
  clearEntryToken();
  btn.disabled = true;
  btn.classList.add('running');
  setAuthResult('登录中...', 'busy');
  try {
    const data = await postJson('/api/auth/login', payload);
    if (!data.entry_token) throw new Error('登录响应缺少 entry_token，请重启后端服务并确认前后端版本一致');
    state.entryToken = data.entry_token;
    if (typeof data.workspace_csv === 'string') saveWorkspaceCsv(data.workspace_csv);
    renderRiskResult(data);
    setAuthStatus(`已登录: ${data.username}`, true);
    setAuthResult('登录成功，3秒后可手动进入工作台', 'ok');
    setEnterButton(false, '3秒后可进入');
    state.enterTimer = window.setTimeout(() => {
      state.enterTimer = null;
      if (!state.entryToken) return;
      setEnterButton(true, '进入工作台');
      setAuthResult('登录成功，请手动点击“进入工作台”', 'ok');
    }, 3000);
  } catch (err) {
    if (err.data) renderRiskResult(err.data);
    setAuthStatus('未登录', false);
    setAuthResult(err.message || '登录失败', 'err');
    clearEntryToken();
  } finally {
    btn.disabled = false;
    btn.classList.remove('running');
  }
}

function enterWorkspace() {
  if (!state.entryToken) {
    setAuthResult('请先登录，等待进入按钮可用后再跳转', 'err');
    return;
  }
  location.href = entryPath(nextPath(), state.entryToken);
}

async function resetAuthState() {
  setAuthStatus('未登录', false);
  setAuthResult(initialAuthMessage(), 'idle');
  clearEntryToken();
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
$('enter-workspace-btn').addEventListener('click', enterWorkspace);
$('login-password').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') login();
});

resetAuthState();
