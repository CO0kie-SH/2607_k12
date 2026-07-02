const state = { rpcId: 1, pending: new Map(), ws: null, latest: null, accessToken: '' };

const $ = (id) => document.getElementById(id);

$('account-info').value = '';

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

function setAccountInfo(payload) {
  if (!payload) {
    $('account-info').value = '';
    return;
  }
  state.latest = payload;
  $('account-info').value = payload.account_info || JSON.stringify(payload.report || payload, null, 2);
  if (payload.report_path) setResult(`已导出: ${payload.report_path}`, 'ok');
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
  const ids = report.workspace_ids || [];
  if (ids.length) return ids.join(', ');
  const items = report.query?.accounts?.data?.items || [];
  const itemIds = items.map(item => item.id).filter(Boolean);
  return itemIds.length ? itemIds.join(', ') : '-';
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

function workspaceDetailLogLines(result) {
  return workspaceDetails(result).map(item => {
    if ((item.type || '').toLowerCase() === 'personal') {
      return logLine(`其中${item.id}为个人空间`);
    }
    return logLine(`其中${item.id}空间的类型为:${item.type || '-'}，空间名为${item.name || '-'}，请检查邮箱或者刷新主页查看该空间`);
  });
}

function appendWorkspaceDetailLogs(result) {
  for (const line of workspaceDetailLogLines(result)) {
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
  const line = logLine(text);
  el.value = el.value ? `${el.value}\n${line}` : line;
  el.scrollTop = el.scrollHeight;
}

function appendProgress(payload) {
  const time = payload.time ? new Date(payload.time).toLocaleTimeString() : new Date().toLocaleTimeString();
  const line = `[${time}] ${payload.message || payload.stage || ''}`;
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

function workspaceIdsFromInput() {
  return $('workspace-id').value
    .split(/\r?\n/)
    .map(item => item.trim())
    .filter(Boolean);
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
    const result = await rpc('k12.inspect_at', { access_token: accessToken, operator_log: queryLog });
    state.accessToken = accessToken;
    setAccountInfo(result);
    setApplyEnabled(true);
    const finalLog = [
      queryLog,
      logLine(`${label}邮箱当前工作区为[${workspaceSummary(result)}]`),
      ...workspaceDetailLogLines(result),
    ].join('\n');
    $('operator-log').value = finalLog;
    await rpc('k12.save_log', { text: finalLog }).catch(() => {});
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
  const workspaceIds = workspaceIdsFromInput();
  if (!workspaceIds.length) {
    setResult('空间ID不能为空', 'err');
    return;
  }

  const btn = $('reload-btn');
  btn.disabled = true;
  btn.classList.add('running');
  appendOperatorLog(`开始申请空间，共${workspaceIds.length}个`);
  setResult('申请空间中...', 'busy');

  try {
    const result = await rpc('k12.apply_workspaces', {
      access_token: state.accessToken,
      workspace_ids: workspaceIds,
      operator_log: $('operator-log').value,
    }, 180000);
    if (result.account_report) setAccountInfo(result.account_report);
    if (result.success) {
      appendOperatorLog(`申请${result.accepted_workspace_id}成功，流程结束`);
      if (result.account_report) appendWorkspaceDetailLogs(result.account_report);
      setResult(`申请成功: ${result.accepted_workspace_id}`, 'ok');
    } else {
      appendOperatorLog('申请空间失败，列表已全部尝试');
      if (result.account_report) appendWorkspaceDetailLogs(result.account_report);
      setResult('申请空间失败，列表已全部尝试', 'err');
    }
    await rpc('k12.save_log', { text: $('operator-log').value }).catch(() => {});
  } catch (err) {
    const message = err.message || err?.error?.message || JSON.stringify(err);
    appendOperatorLog(`申请空间失败: ${message}`);
    setResult(`申请空间失败: ${message}`, 'err');
  } finally {
    btn.classList.remove('running');
    setApplyEnabled(Boolean(state.accessToken));
  }
}

function connectWs() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  state.ws = ws;

  ws.onopen = async () => {
    setStatus('WebSocket 已连接', true);
  };
  ws.onclose = () => {
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
    if (msg.method === 'k12.log') setResult(`日志已保存: ${msg.params.path}`, 'ok');
    if (msg.method === 'k12.apply_progress') {
      appendProgress(msg.params);
      if (msg.params.message) appendOperatorLog(msg.params.message);
    }
  };
}

$('inspect-btn').addEventListener('click', submitAt);
$('reload-btn').addEventListener('click', applyWorkspaces);

connectWs();
