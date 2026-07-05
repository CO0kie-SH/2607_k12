const state = { rpcId: 1, pending: new Map(), ws: null, latest: null, accessToken: '', profile: null };
let sessionEnding = false;
const PERSONAL_SPACE_AT_MESSAGE = '请用个人空间的AT进行申请';
const DEACTIVATED_WORKSPACE_MESSAGE = '请勿使用停用的空间进行申请';
const ADDED_WORKSPACE_MARK = '■■■【新增空间】■■■';
const WORKSPACE_CSV_STORAGE_KEY = 'k12_workspace_csv';
const LOCAL_PATH_RE = /[A-Za-z]:\\[^\s\r\n"']+/g;

const $ = (id) => document.getElementById(id);

$('account-info').value = '';

function initializeWorkspaceCsv() {
  try {
    const workspaceCsv = sessionStorage.getItem(WORKSPACE_CSV_STORAGE_KEY);
    if (workspaceCsv !== null) $('workspace-id').value = workspaceCsv;
  } catch {
    // Keep the static default, which is intentionally empty.
  }
}

initializeWorkspaceCsv();

function setStatus(text, ok = false) {
  const el = $('status');
  el.textContent = text;
  el.className = `status ${ok ? 'connected' : 'disconnected'}`;
}

function setResult(text, kind = 'idle') {
  const el = $('result-line');
  el.textContent = text;
  el.className = `result-line ${kind}`;
}

function sanitizeLocalPaths(text) {
  return String(text || '').replace(LOCAL_PATH_RE, '[server-path]');
}

function closeCurrentWebSocket() {
  if (!state.ws || state.ws.readyState > WebSocket.OPEN) return;
  state.ws.close(1000, 'session ended');
}

function sendLogoutBeacon() {
  if (sessionEnding) return;
  sessionEnding = true;
  closeCurrentWebSocket();
  try {
    if (navigator.sendBeacon) {
      navigator.sendBeacon('/api/auth/logout', new Blob([''], { type: 'text/plain' }));
      return;
    }
  } catch {
    // keepalive fetch below is the fallback path.
  }
  fetch('/api/auth/logout', {
    method: 'POST',
    credentials: 'same-origin',
    keepalive: true,
  }).catch(() => {});
}

async function endSessionAndReturnHome(reason = '会话已结束') {
  if (sessionEnding) {
    location.replace('/?session=expired');
    return;
  }
  sessionEnding = true;
  setStatus(reason, false);
  setApplyEnabled(false);
  closeCurrentWebSocket();
  try {
    await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
  } finally {
    location.replace('/?session=expired');
  }
}

function setAccountInfo(payload) {
  if (!payload) {
    $('account-info').value = '';
    return;
  }
  state.latest = payload;
  $('account-info').value = sanitizeLocalPaths(payload.account_info || JSON.stringify(payload.report || payload, null, 2));
  if (payload.report || payload.account_info) setResult('查询完成，报告已保存', 'ok');
}

function setApplyEnabled(enabled) {
  const btn = $('reload-btn');
  btn.disabled = !enabled;
  btn.classList.toggle('inactive-btn', !enabled);
}

function decodeTokenProfile(accessToken) {
  try {
    const payload = accessToken.split('.')[1];
    const normalized = payload.replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized + '='.repeat((4 - normalized.length % 4) % 4);
    const claims = JSON.parse(decodeURIComponent(escape(atob(padded))));
    const profile = claims['https://api.openai.com/profile'] || {};
    const auth = claims['https://api.openai.com/auth'] || {};
    return {
      email: profile.email || '',
      phone: profile.phone_number || '',
      accountId: auth.chatgpt_account_id || '',
    };
  } catch {
    return { email: '', phone: '', accountId: '' };
  }
}

function accountLabel(profile) {
  return profile.email || profile.phone || profile.accountId || '未知账号';
}

function emailDomain(email) {
  const parts = String(email || '').toLowerCase().split('@');
  return parts.length === 2 ? parts[1].trim() : '';
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

function workspaceSummary(result) {
  const report = result?.report || {};
  const ids = workspaceIdsFromResult(result);
  if (ids.length) return ids.join(', ');
  return '-';
}

function workspaceIdsFromResult(result) {
  const report = result?.report || {};
  const ids = Array.isArray(report.workspace_ids) ? report.workspace_ids : [];
  if (ids.length) return ids.filter(Boolean);
  const items = report.query?.accounts?.data?.items || [];
  return items.map(item => item?.id).filter(Boolean);
}

function workspaceDiff(beforeIds, result) {
  const before = beforeIds instanceof Set ? beforeIds : new Set(beforeIds || []);
  return workspaceIdsFromResult(result).filter(id => !before.has(id));
}

function workspaceDetails(result) {
  const report = result?.report || {};
  if (Array.isArray(report.workspace_details) && report.workspace_details.length) {
    return report.workspace_details;
  }
  const items = report.query?.accounts?.data?.items || [];
  return items
    .filter(item => item && item.id)
    .map(item => ({
      id: item.id,
      type: item.structure || item.type || '-',
      name: item.name || '-',
      role: item.current_user_role || '-',
    }));
}

function workspaceDetailLogLines(result, highlightIds = []) {
  const highlighted = highlightIds instanceof Set ? highlightIds : new Set(highlightIds);
  return workspaceDetails(result).map(item => {
    const prefix = highlighted.has(item.id) ? `${ADDED_WORKSPACE_MARK}` : '';
    if ((item.type || '').toLowerCase() === 'personal') {
      return logLine(`${prefix}其中${item.id}为个人空间`);
    }
    return logLine(`${prefix}其中${item.id}空间的类型为:${item.type || '-'}，空间名为${item.name || '-'}，请检查邮箱或者刷新主页查看该空间`);
  });
}

function appendWorkspaceDetailLogs(result, highlightIds = []) {
  const highlighted = new Set(highlightIds);
  for (const line of workspaceDetailLogLines(result, highlighted)) {
    const el = $('operator-log');
    el.value = el.value ? `${el.value}\n${line}` : line;
    el.scrollTop = el.scrollHeight;
  }
}

function clearProgress() {
  $('progress-log').textContent = '';
}

function appendOperatorLog(text) {
  const el = $('operator-log');
  const line = logLine(sanitizeLocalPaths(text));
  el.value = el.value ? `${el.value}\n${line}` : line;
  el.scrollTop = el.scrollHeight;
}

function hasDeactivatedWorkspace(result = state.latest) {
  const report = result?.report || {};
  const details = Array.isArray(report.workspace_details) ? report.workspace_details : [];
  if (details.some(item => String(item?.type || '').toLowerCase() === 'deactivated_workspace')) {
    return true;
  }

  function walk(value, depth = 0) {
    if (depth > 8 || value == null) return false;
    if (typeof value === 'string') return value.toLowerCase() === 'deactivated_workspace';
    if (Array.isArray(value)) return value.some(item => walk(item, depth + 1));
    if (typeof value === 'object') return Object.values(value).some(item => walk(item, depth + 1));
    return false;
  }

  return walk(report.query?.accounts?.data);
}

function openExternalPage(url) {
  window.open(url, '_blank', 'noopener,noreferrer');
}

function openChatGptPage() {
  appendOperatorLog('打开 ChatGPT session 网页');
  openExternalPage('https://chatgpt.com/api/auth/session');
}

function leaveSpacePage() {
  const ids = Array.from(currentWorkspaceIds());
  if (!ids.length) {
    const message = '请先提交查询并成功获取当前账号空间信息';
    appendOperatorLog(message);
    setResult(message, 'err');
    return;
  }
  appendOperatorLog(`准备退出空间，当前账号空间: ${ids.join(', ')}`);
  setResult('已打开账号设置页，请在网页中确认退出空间', 'ok');
  openExternalPage('https://chatgpt.com/#settings/Account');
}

function appendProgress(payload) {
  const time = payload.time ? new Date(payload.time).toLocaleTimeString() : new Date().toLocaleTimeString();
  const line = `[${time}] ${sanitizeLocalPaths(payload.message || payload.stage || '')}`;
  const el = $('progress-log');
  el.textContent = el.textContent && el.textContent !== '等待提交' ? `${el.textContent}\n${line}` : line;
  el.scrollTop = el.scrollHeight;
}

function rpc(method, params = {}, timeoutMs = 120000) {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    return Promise.reject(new Error('WebSocket 未连接'));
  }
  const id = state.rpcId++;
  state.ws.send(JSON.stringify({ jsonrpc: '2.0', id, method, params }));
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      state.pending.delete(id);
      reject(new Error(`RPC 请求超时: ${method}`));
    }, timeoutMs);
    state.pending.set(id, { resolve, reject, timer });
  });
}

function workspaceEntriesFromInput() {
  return $('workspace-id').value
    .split(/\r?\n/)
    .map(item => item.trim())
    .filter(Boolean)
    .filter(line => line.split(',', 1)[0].trim().toLowerCase() !== 'workspace_id')
    .map(line => {
      const parts = line.split(',').map(item => item.trim());
      return {
        id: parts[0] || '',
        planType: parts[1] || '',
        emailSuffix: (parts[2] || '').toLowerCase(),
        available: parts[3] ? parts[3].toLowerCase() === 'true' : true,
        raw: line,
      };
    });
}

function workspaceIdsForProfile(profile) {
  const domain = emailDomain(profile?.email || '');
  const entries = workspaceEntriesFromInput();
  const matched = entries.filter(item => {
    if (!item.id || !item.available) return false;
    if (!item.emailSuffix) return true;
    return Boolean(domain) && domain === item.emailSuffix;
  });
  return {
    domain,
    skipped: entries.length - matched.length,
    ids: matched.map(item => item.id),
  };
}

function selfWorkspaceIdsForProfile(profile) {
  const { ids } = workspaceIdsForProfile(profile);
  const accountId = String(profile?.accountId || '').trim();
  if (!accountId) return [];
  return Array.from(new Set(ids.filter(id => id === accountId)));
}

function applyGuardMessages(profile, result = state.latest) {
  const messages = [];
  if (hasDeactivatedWorkspace(result)) {
    messages.push(DEACTIVATED_WORKSPACE_MESSAGE);
  }
  return messages;
}

function currentWorkspaceIds() {
  const report = state.latest?.report || {};
  const ids = Array.isArray(report.workspace_ids) ? report.workspace_ids : [];
  return new Set(ids.filter(Boolean));
}

async function submitAt() {
  const accessToken = $('access-token').value.trim();
  if (!accessToken.startsWith('eyJ')) {
    setResult('AT 必须以 eyJ 开头', 'err');
    return;
  }
  const profile = decodeTokenProfile(accessToken);
  const label = accountLabel(profile);
  const email = profile.email || '-';
  const queryLog = logLine(`查询${tokenLabel(accessToken)}的账号邮箱为${email}`);
  $('operator-log').value = queryLog;
  $('inspect-btn').disabled = true;
  $('inspect-btn').classList.add('running');
  setApplyEnabled(false);
  clearProgress();
  setResult('查询中...', 'busy');
  try {
    const result = await rpc('k12.inspect_at', { access_token: accessToken, operator_log: sanitizeLocalPaths(queryLog) });
    state.accessToken = accessToken;
    state.profile = profile;
    setAccountInfo(result);
    const guardMessages = applyGuardMessages(profile, result);
    const selfMatchedIds = selfWorkspaceIdsForProfile(profile);
    setApplyEnabled(!guardMessages.length);
    const finalLog = [
      queryLog,
      logLine(`${label}邮箱当前工作区为[${workspaceSummary(result)}]`),
      ...workspaceDetailLogLines(result),
      ...selfMatchedIds.map(id => logLine(`待申请列表包含当前账号ID ${id}，申请时会跳过该ID；${PERSONAL_SPACE_AT_MESSAGE}`)),
      ...guardMessages.map(message => logLine(message)),
    ].join('\n');
    $('operator-log').value = finalLog;
    if (guardMessages.length) setResult(guardMessages.join('；'), 'err');
    await rpc('k12.save_log', { text: sanitizeLocalPaths(finalLog) }).catch(() => {});
  } catch (err) {
    const message = err.message || err?.error?.message || JSON.stringify(err);
    setResult(`查询失败: ${message}`, 'err');
  } finally {
    $('inspect-btn').disabled = false;
    $('inspect-btn').classList.remove('running');
  }
}

async function applyWorkspaces() {
  if (!state.accessToken) {
    setResult('请先提交查询并成功获取账号信息', 'err');
    return;
  }
  const profile = state.profile || decodeTokenProfile(state.accessToken);
  const { domain, skipped, ids: workspaceIds } = workspaceIdsForProfile(profile);
  const guardMessages = applyGuardMessages(profile);
  if (guardMessages.length) {
    guardMessages.forEach(message => appendOperatorLog(message));
    setResult(guardMessages.join('；'), 'err');
    setApplyEnabled(false);
    return;
  }
  if (!workspaceIds.length) {
    const message = domain
      ? `当前邮箱后缀 ${domain} 不匹配任何可申请空间`
      : '当前 AT 未解析到邮箱，无法按邮箱后缀匹配空间';
    appendOperatorLog(message);
    setResult(message, 'err');
    return;
  }

  const existingWorkspaceIds = currentWorkspaceIds();
  const beforeApplyWorkspaceIds = new Set(existingWorkspaceIds);
  const accountId = String(profile?.accountId || '').trim();
  const selfMatchedIds = accountId ? Array.from(new Set(workspaceIds.filter(id => id === accountId))) : [];
  const candidateWorkspaceIds = accountId ? workspaceIds.filter(id => id !== accountId) : workspaceIds;
  const existingMatchedIds = candidateWorkspaceIds.filter(id => existingWorkspaceIds.has(id));
  const applyWorkspaceIds = candidateWorkspaceIds.filter(id => !existingWorkspaceIds.has(id));

  if (selfMatchedIds.length) {
    appendOperatorLog(`跳过账号自身ID ${selfMatchedIds.join(', ')}，${PERSONAL_SPACE_AT_MESSAGE}`);
  }

  if (existingMatchedIds.length) {
    appendOperatorLog(`跳过已存在空间${existingMatchedIds.join(', ')}`);
  }

  if (!applyWorkspaceIds.length) {
    const message = existingMatchedIds.length
      ? '除账号自身外，匹配的可申请空间已存在于当前账号，不再提交后端申请'
      : selfMatchedIds.length
        ? '匹配的可申请空间仅包含当前账号ID，不提交后端申请'
        : domain
          ? `当前邮箱后缀 ${domain} 不匹配任何可申请空间`
          : '当前 AT 未解析到邮箱，无法按邮箱后缀匹配空间';
    appendOperatorLog(message);
    setResult(message, existingMatchedIds.length ? 'ok' : 'err');
    return;
  }

  const btn = $('reload-btn');
  btn.disabled = true;
  btn.classList.add('running');
  appendOperatorLog(`邮箱后缀${domain || '-'}匹配可申请空间${workspaceIds.length}个${skipped ? `，跳过${skipped}个` : ''}${selfMatchedIds.length ? `，跳过账号自身${selfMatchedIds.length}个` : ''}${existingMatchedIds.length ? `，已存在${existingMatchedIds.length}个` : ''}`);
  appendOperatorLog(`开始申请空间，共${applyWorkspaceIds.length}个`);
  setResult('申请空间中...', 'busy');

  try {
    const result = await rpc('k12.apply_workspaces', {
      access_token: state.accessToken,
      workspace_ids: applyWorkspaceIds,
      operator_log: sanitizeLocalPaths($('operator-log').value),
    }, 180000);
    const addedWorkspaceIds = result.account_report ? workspaceDiff(beforeApplyWorkspaceIds, result.account_report) : [];
    if (result.account_report) setAccountInfo(result.account_report);
    if (result.success) {
      appendOperatorLog(`申请${result.accepted_workspace_id}成功，流程结束`);
      if (addedWorkspaceIds.length) appendOperatorLog(`${ADDED_WORKSPACE_MARK}刷新后检测到新增空间: ${addedWorkspaceIds.join(', ')}`);
      if (result.account_report) appendWorkspaceDetailLogs(result.account_report, addedWorkspaceIds);
      setResult(addedWorkspaceIds.length ? `${ADDED_WORKSPACE_MARK}申请成功，新增空间: ${addedWorkspaceIds.join(', ')}` : `申请成功: ${result.accepted_workspace_id}`, 'ok');
    } else if (addedWorkspaceIds.length) {
      appendOperatorLog(`${ADDED_WORKSPACE_MARK}申请接口返回失败，但刷新后检测到新增空间: ${addedWorkspaceIds.join(', ')}`);
      if (result.account_report) appendWorkspaceDetailLogs(result.account_report, addedWorkspaceIds);
      setResult(`${ADDED_WORKSPACE_MARK}检测到新增空间: ${addedWorkspaceIds.join(', ')}`, 'ok');
    } else {
      appendOperatorLog('申请空间失败，列表已全部尝试');
      if (result.account_report) appendWorkspaceDetailLogs(result.account_report);
      setResult('申请空间失败，列表已全部尝试', 'err');
    }
    await rpc('k12.save_log', { text: sanitizeLocalPaths($('operator-log').value) }).catch(() => {});
  } catch (err) {
    const message = err.message || err?.error?.message || JSON.stringify(err);
    appendOperatorLog(`申请空间失败: ${message}`);
    setResult(`申请空间失败: ${message}`, 'err');
  } finally {
    btn.classList.remove('running');
    setApplyEnabled(Boolean(state.accessToken));
  }
}

async function logout() {
  await endSessionAndReturnHome('已退出登录');
}

function connectWs() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  state.ws = ws;

  ws.onopen = async () => {
    setStatus('WebSocket 已连接', true);
  };
  ws.onclose = (event) => {
    if (sessionEnding) return;
    if (event.code === 1008 || event.code === 1006) {
      setStatus('未登录或会话已过期', false);
      if (!sessionEnding) {
        window.setTimeout(() => location.replace('/?session=expired'), 500);
      }
      return;
    }
    setStatus('连接断开，重连中...', false);
    setTimeout(connectWs, 1500);
  };
  ws.onerror = () => setStatus('WebSocket 错误', false);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && state.pending.has(msg.id)) {
      const pending = state.pending.get(msg.id);
      state.pending.delete(msg.id);
      window.clearTimeout(pending.timer);
      msg.error ? pending.reject(msg.error) : pending.resolve(msg.result);
      return;
    }
    if (msg.method === 'server.hello') $('account-info').value = '';
    if (msg.method === 'k12.progress') appendProgress(msg.params);
    if (msg.method === 'k12.report') setAccountInfo(msg.params);
    if (msg.method === 'k12.log') setResult('日志已保存', 'ok');
    if (msg.method === 'k12.apply_progress') {
      appendProgress(msg.params);
      if (msg.params.message) appendOperatorLog(msg.params.message);
    }
  };
}

$('inspect-btn').addEventListener('click', submitAt);
$('reload-btn').addEventListener('click', applyWorkspaces);
$('open-web-btn').addEventListener('click', openChatGptPage);
$('leave-space-btn').addEventListener('click', leaveSpacePage);
$('logout-btn').addEventListener('click', logout);
window.addEventListener('pagehide', sendLogoutBeacon);

connectWs();
