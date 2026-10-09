// ===================== UI 辅助 =====================

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024*1024) return (bytes/1024).toFixed(1) + ' KB';
  return (bytes/1024/1024).toFixed(1) + ' MB';
}

function fileIcon(name) {
  const ext = name.split('.').pop().toLowerCase();
  const map = { md:'📝', txt:'📝', py:'🐍', js:'📜', html:'🌐', css:'🎨', json:'📋', yml:'📋', yaml:'📋', sh:'⚙️', log:'📊' };
  return map[ext] || '📄';
}

function toast(msg, type) {
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2500);
}

function formatTime(ts) {
  if (!ts) return '';
  const date = new Date(ts * 1000);
  return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour:'2-digit', minute:'2-digit', second:'2-digit' });
}

function mergeWorkspaceObservation(workspaceId, observation) {
  if (!workspaceId || !observation) return;
  workspaceObservations[workspaceId] = {
    workspace_id: workspaceId,
    unread_hooks: Number(observation.unread_hooks || 0),
    terminals: Array.isArray(observation.terminals) ? observation.terminals : [],
    recent_hook_events: Array.isArray(observation.recent_hook_events) ? observation.recent_hook_events : [],
  };
  const index = workspaceList.findIndex(s => s.id === workspaceId);
  if (index >= 0) {
    workspaceList[index] = { ...workspaceList[index], observation: workspaceObservations[workspaceId] };
  }
}

function mergeTerminalObservation(workspaceId, terminalId, observation) {
  if (!workspaceId || !terminalId || !observation) return;
  const workspaceObs = workspaceObservation(workspaceId);
  const terminals = Array.isArray(workspaceObs.terminals) ? workspaceObs.terminals.slice() : [];
  const next = {
    ...observation,
    terminal_id: observation.terminal_id || terminalId,
    unread_hooks: Number(observation.unread_hooks || 0),
    hook_events: Array.isArray(observation.hook_events) ? observation.hook_events : [],
  };
  const index = terminals.findIndex(item => item.terminal_id === terminalId);
  if (index >= 0) {
    terminals[index] = { ...terminals[index], ...next };
  } else {
    terminals.push(next);
  }
  const recent = [];
  for (const item of terminals) {
    recent.push(...(item.hook_events || []));
  }
  recent.sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
  mergeWorkspaceObservation(workspaceId, {
    ...workspaceObs,
    terminals,
    unread_hooks: terminals.reduce((sum, item) => sum + Number(item.unread_hooks || 0), 0),
    recent_hook_events: recent.slice(0, 10),
  });
}

function applyWorkspaceObservations(observations = {}) {
  for (const [workspaceId, observation] of Object.entries(observations || {})) {
    mergeWorkspaceObservation(workspaceId, observation);
  }
}

function workspaceObservation(workspaceId = activeWorkspaceId) {
  const fromCache = workspaceObservations[workspaceId];
  if (fromCache) return fromCache;
  const workspace = workspaceList.find(s => s.id === workspaceId);
  return workspace?.observation || {
    workspace_id: workspaceId,
    unread_hooks: 0,
    terminals: [],
    recent_hook_events: [],
  };
}

function workspaceUnreadHooks(workspaceId = activeWorkspaceId) {
  return Number(workspaceObservation(workspaceId).unread_hooks || 0);
}

function latestCodexTurnEvent(terminal, workspaceId = terminal?.workspace_id || activeWorkspaceId) {
  const observation = terminal?.observation || terminalObservation(workspaceId, terminal?.id);
  const events = Array.isArray(observation?.hook_events) ? observation.hook_events : [];
  const threadId = String(terminal?.thread_id || '');
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event?.source !== 'codex-hook') continue;
    if (event.session_id && threadId && event.session_id !== threadId) continue;
    return event;
  }
  return null;
}

function terminalRunState(terminal) {
  if (!terminal || !terminal.alive) return 'dead';
  const turnEvent = latestCodexTurnEvent(terminal);
  if (turnEvent?.status === 'running') return 'busy';
  if (turnEvent?.status === 'done' || turnEvent?.status === 'failed') return terminal.codex_active ? 'idle' : 'shell';
  return terminal.codex_active ? 'unknown' : 'shell';
}

function terminalActivityText(terminal) {
  const turnEvent = latestCodexTurnEvent(terminal);
  if (turnEvent?.status === 'running') return 'Codex 正在工作';
  if (turnEvent?.status === 'done') return 'Codex 已完成';
  if (turnEvent?.status === 'failed') return 'Codex 已结束';
  return '';
}

function terminalHasUnreadCodexCompletion(terminal, workspaceId = terminal?.workspace_id || activeWorkspaceId) {
  const event = latestCodexTurnEvent(terminal, workspaceId);
  const observation = terminal?.observation || terminalObservation(workspaceId, terminal?.id);
  return Boolean(
    event
    && (event.status === 'done' || event.status === 'failed')
    && Number(observation?.unread_hooks || 0) > 0
  );
}

function terminalIsCurrentlyDisplayed(terminal, workspaceId = terminal?.workspace_id || activeWorkspaceId) {
  return workspaceId === activeWorkspaceId && terminal?.id === activeTerminalId;
}

function terminalNeedsCompletionAttention(terminal, workspaceId = terminal?.workspace_id || activeWorkspaceId) {
  return terminalHasUnreadCodexCompletion(terminal, workspaceId)
    && !terminalIsCurrentlyDisplayed(terminal, workspaceId);
}

function terminalVisualState(terminal, workspaceId = terminal?.workspace_id || activeWorkspaceId) {
  if (terminalNeedsCompletionAttention(terminal, workspaceId)) return 'attention';
  return terminalRunState(terminal);
}

function workspaceRunState(workspace) {
  if (workspace.exists === false) return 'missing';
  const terminals = Array.isArray(workspace.terminals) ? workspace.terminals : [];
  if (terminals.some(terminal => terminalRunState(terminal) === 'busy')) return 'busy';
  return 'idle';
}

function workspaceHasUnreadCodexCompletion(workspace) {
  const terminals = Array.isArray(workspace?.terminals) ? workspace.terminals : [];
  return terminals.some(terminal => terminalHasUnreadCodexCompletion(terminal, workspace.id));
}

function workspaceVisualState(workspace) {
  const terminals = Array.isArray(workspace?.terminals) ? workspace.terminals : [];
  if (terminals.some(terminal => terminalNeedsCompletionAttention(terminal, workspace.id))) {
    return 'attention';
  }
  return workspaceRunState(workspace);
}

function terminalObservation(workspaceId = activeWorkspaceId, terminalId = activeTerminalId) {
  const observation = workspaceObservation(workspaceId);
  return (observation.terminals || []).find(item => item.terminal_id === terminalId) || {
    terminal_id: terminalId,
    unread_hooks: 0,
    hook_events: [],
  };
}

const completionReadInFlight = new Set();

function clearCompletionUnreadCache(workspaceId, terminalId = '') {
  const observation = workspaceObservation(workspaceId);
  const terminals = Array.isArray(observation.terminals) ? observation.terminals : [];
  for (const item of terminals) {
    if (terminalId && item.terminal_id !== terminalId) continue;
    item.unread_hooks = 0;
  }
  observation.unread_hooks = terminals.reduce(
    (sum, item) => sum + Number(item.unread_hooks || 0),
    0,
  );
  mergeWorkspaceObservation(workspaceId, observation);

  const workspace = workspaceList.find(item => item.id === workspaceId);
  for (const terminal of workspace?.terminals || []) {
    if (terminalId && terminal.id !== terminalId) continue;
    if (terminal.observation) terminal.observation.unread_hooks = 0;
  }
}

async function markCodexCompletionSeen(workspaceId, terminalId = '') {
  if (!workspaceId) return false;
  const key = workspaceId + ':' + (terminalId || '*');
  if (completionReadInFlight.has(key)) return false;
  completionReadInFlight.add(key);
  clearCompletionUnreadCache(workspaceId, terminalId);
  updateRuntimeStatusIndicators();
  try {
    await backendApi.observations.markRead(workspaceId, terminalId);
    return true;
  } catch(e) {
    console.warn('mark Codex completion seen failed', e);
    return false;
  } finally {
    completionReadInFlight.delete(key);
  }
}

function acknowledgeDisplayedCodexCompletion() {
  const terminal = activeTerminalInfo();
  if (!terminalHasUnreadCodexCompletion(terminal, activeWorkspaceId)) return;
  markCodexCompletionSeen(activeWorkspaceId, activeTerminalId);
}

// ===================== Workspaces & Stats =====================

function terminalStorageKey(workspaceId = activeWorkspaceId) {
  return 'active_terminal_' + (workspaceId || DEFAULT_WORKSPACE_ID);
}

function mergeWorkspaceSummary(workspace) {
  if (!workspace || !workspace.id) return;
  const index = workspaceList.findIndex(s => s.id === workspace.id);
  if (index >= 0) {
    const existing = workspaceList[index];
    const merged = { ...existing, ...workspace };
    if (!Array.isArray(merged.terminals) && Array.isArray(existing.terminals)) {
      merged.terminals = existing.terminals;
    }
    if (!merged.terminal && existing.terminal) {
      merged.terminal = existing.terminal;
    }
    workspaceList[index] = merged;
  } else {
    workspaceList.push(workspace);
  }
  if (workspace.observation) {
    mergeWorkspaceObservation(workspace.id, workspace.observation);
  }
}

function applyWorkspaceTerminalSnapshot(workspaceId, data) {
  if (!workspaceId || !data) return [];
  const workspace = data.workspace || workspaceList.find(s => s.id === workspaceId) || { id: workspaceId, name: workspaceId };
  const terminals = (Array.isArray(data.terminals) ? data.terminals : [])
    .map(terminal => ({
      ...terminal,
      id: terminal.id || DEFAULT_TERMINAL,
      name: terminal.name || terminal.id || DEFAULT_TERMINAL,
      workspace_id: terminal.workspace_id || workspaceId,
    }));
  const primary = terminals.find(t => t.id === DEFAULT_TERMINAL) || terminals[0] || null;
  mergeWorkspaceSummary({
    ...workspace,
    id: workspace.id || workspaceId,
    terminals,
    terminal: primary || workspace.terminal || null,
  });
  for (const terminal of terminals) {
    if (terminal.observation) mergeTerminalObservation(workspaceId, terminal.id, terminal.observation);
  }
  return terminals;
}

async function syncWorkspaceTerminals(workspaceId = activeWorkspaceId, opts = {}) {
  if (!workspaceId) return null;
  try {
    const data = await backendApi.terminals.list(workspaceId);
    if (!data || data.error) {
      console.warn('workspace terminal sync failed', data?.error || 'empty response');
      return null;
    }
    const terminals = applyWorkspaceTerminalSnapshot(workspaceId, data);
    if (workspaceId === activeWorkspaceId) normalizeActiveTerminal();
    if (opts.render !== false) {
      renderWorkspaceSwitch();
      renderTerminalTabs();
    }
    return terminals;
  } catch(e) {
    console.warn('workspace terminal sync failed', e);
    return null;
  }
}

function primaryTerminalForWorkspace(workspace) {
  const terminals = Array.isArray(workspace.terminals) ? workspace.terminals : [];
  return terminals.find(t => t.id === DEFAULT_TERMINAL)
    || terminals.find(t => t.alive || t.usable)
    || (Array.isArray(workspace.terminals) ? null : workspace.terminal)
    || {};
}

function activeWorkspaceTerminals() {
  const workspace = activeWorkspaceInfo();
  const terminals = [];
  const seen = new Set();
  const hasRuntimeTerminals = Array.isArray(workspace.terminals);
  const addTerminal = terminal => {
    if (!terminal) return;
    const id = terminal.id || DEFAULT_TERMINAL;
    if (seen.has(id)) return;
    seen.add(id);
    terminals.push({
      ...terminal,
      id,
      name: terminal.name || id,
      workspace_id: terminal.workspace_id || activeWorkspaceId,
    });
  };
  if (hasRuntimeTerminals) {
    workspace.terminals.forEach(addTerminal);
  } else {
    addTerminal(workspace.terminal);
  }
  if (!terminals.length && activeWorkspaceId && !hasRuntimeTerminals) {
    addTerminal({
      id: DEFAULT_TERMINAL,
      name: DEFAULT_TERMINAL,
      workspace_id: activeWorkspaceId,
      alive: false,
      usable: false,
      pid: null,
      cwd: workspace.cwd || '',
    });
  }
  terminals.sort((a, b) => {
    if (a.id === DEFAULT_TERMINAL) return -1;
    if (b.id === DEFAULT_TERMINAL) return 1;
    return String(a.name || a.id).localeCompare(String(b.name || b.id));
  });
  return terminals;
}

function normalizeActiveTerminal() {
  if (!activeWorkspaceId) {
    activeTerminalId = '';
    return activeTerminalId;
  }
  const terminals = activeWorkspaceTerminals();
  const ids = terminals.map(t => t.id);
  if (!ids.length) {
    activeTerminalId = '';
    localStorage.removeItem(terminalStorageKey(activeWorkspaceId));
    return activeTerminalId;
  }
  const saved = localStorage.getItem(terminalStorageKey(activeWorkspaceId));
  if (saved && ids.includes(saved)) {
    activeTerminalId = saved;
  }
  if (!ids.includes(activeTerminalId)) {
    activeTerminalId = (terminals.find(t => t.id === DEFAULT_TERMINAL) || terminals[0] || { id: DEFAULT_TERMINAL }).id;
  }
  localStorage.setItem(terminalStorageKey(activeWorkspaceId), activeTerminalId);
  return activeTerminalId;
}

function setActiveTerminal(terminalId) {
  activeTerminalId = terminalId === '' ? '' : (terminalId || DEFAULT_TERMINAL);
  if (activeWorkspaceId) {
    if (activeTerminalId) {
      localStorage.setItem(terminalStorageKey(activeWorkspaceId), activeTerminalId);
    } else {
      localStorage.removeItem(terminalStorageKey(activeWorkspaceId));
    }
  }
}

function activeTerminalInfo() {
  const terminals = activeWorkspaceTerminals();
  return terminals.find(t => t.id === activeTerminalId)
    || terminals[0]
    || { id: DEFAULT_TERMINAL, name: DEFAULT_TERMINAL, workspace_id: activeWorkspaceId };
}

function terminalLabel(terminal = activeTerminalInfo()) {
  const threadId = String(terminal?.thread_id || '').trim();
  const conversation = threadId ? codexHistoryById.get(threadId) : null;
  const conversationTitle = String(conversation?.title || terminal?.thread_title || '').trim();
  if (threadId && conversationTitle) return conversationTitle;
  return terminal.name || terminal.id || DEFAULT_TERMINAL;
}

function renderTerminalTabs() {
  const el = document.getElementById('terminalTabs');
  if (!el) return;
  if (!activeWorkspaceId) {
    el.innerHTML = '<div class="empty-workspaces">请先新增工作区</div>';
    return;
  }
  normalizeActiveTerminal();
  const terminals = activeWorkspaceTerminals();
  if (!terminals.length) {
    el.innerHTML = '<div class="empty-workspaces">没有终端</div>';
    return;
  }
  el.innerHTML = terminals.map(terminal => {
    const isActive = terminal.id === activeTerminalId;
    const active = isActive ? ' active' : '';
    const state = terminalVisualState(terminal, activeWorkspaceId);
    const pid = terminal.pid ? ` · pid ${terminal.pid}` : '';
    const activity = terminalActivityText(terminal);
    const renameHint = isActive ? ' · 再次点击可重命名' : '';
    const attention = state === 'attention' ? ' · 已完成，点击查看' : '';
    const title = `${activeWorkspaceLabel()} / ${terminalLabel(terminal)}${pid}${activity ? ' · ' + activity : ''}${attention}${renameHint}`;
    return `<button class="terminal-tab state-${state}${active}" type="button" data-terminal-id="${escapeHtml(terminal.id)}" title="${escapeHtml(title)}">
      <span class="workspace-dot ${state}"></span>
      <span class="terminal-tab-name">${escapeHtml(terminalLabel(terminal))}</span>
      <span class="tab-close" role="button" tabindex="0" data-close-terminal="${escapeHtml(terminal.id)}" title="关闭终端" aria-label="关闭终端">${CLOSE_ICON_SVG}</span>
    </button>`;
  }).join('');
}

function upsertTerminalSummary(summary) {
  if (!summary) return;
  const terminal = {
    ...summary,
    id: summary.id || DEFAULT_TERMINAL,
    name: summary.name || summary.id || DEFAULT_TERMINAL,
    workspace_id: summary.workspace_id || activeWorkspaceId,
  };
  const workspaceId = terminal.workspace_id;
  const index = workspaceList.findIndex(s => s.id === workspaceId);
  if (index < 0) return;
  const workspace = { ...workspaceList[index] };
  const terminals = Array.isArray(workspace.terminals) ? workspace.terminals.slice() : [];
  const terminalIndex = terminals.findIndex(t => t.id === terminal.id);
  if (terminalIndex >= 0) {
    terminals[terminalIndex] = { ...terminals[terminalIndex], ...terminal };
  } else {
    terminals.push(terminal);
  }
  workspace.terminals = terminals;
  if (!workspace.terminal || workspace.terminal.id === terminal.id || terminal.id === DEFAULT_TERMINAL) {
    workspace.terminal = terminal;
  }
  workspaceList[index] = workspace;
  if (terminal.observation) {
    mergeTerminalObservation(workspaceId, terminal.id, terminal.observation);
  }
}

function switchTerminal(terminalId) {
  if (!activeWorkspaceId) return;
  if (!terminalId) {
    showNoTerminalState();
    return;
  }
  const nextTerminal = terminalId || DEFAULT_TERMINAL;
  if (typeof publishTerminalSnapshot === 'function') {
    publishTerminalSnapshot(activeTerminalView(), { force: true }).catch(() => false);
  }
  setActiveTerminal(nextTerminal);
  termConnected = false;
  const nextView = typeof activateTerminalView === 'function' ? activateTerminalView() : null;
  renderTerminalTabs();
  showTerminalArea();
  setTermStatus(false, '连接中...');
  if (termSocket && termSocket.connected) {
    attachTerminal(true);
  } else {
    connectTerminal();
  }
  try { nextView?.term?.focus(); } catch(e) {}
  const workspaceAtSwitch = activeWorkspaceId;
  syncWorkspaceTerminals(workspaceAtSwitch, { render: false }).then(() => {
    if (workspaceAtSwitch !== activeWorkspaceId || nextTerminal !== activeTerminalId) return;
    renderWorkspaceSwitch();
    renderTerminalTabs();
  });
}

function showNoTerminalState(message = '没有终端') {
  setActiveTerminal('');
  termConnected = false;
  renderTerminalTabs();
  setTermStatus(false, message);
  document.getElementById('termCwdLabel').textContent = '--';
  if (typeof showTerminalEmptyState === 'function') {
    showTerminalEmptyState(message, activeWorkspaceId ? '点击“+ 终端”新建一个会话。' : '请先新增工作区。');
  } else if (term) {
    resetTerminalDisplay();
    term.write('\x1b[90m没有打开的终端。点击“+ 终端”新建一个会话。\x1b[0m\r\n');
  }
}

function defaultTerminalSessionName() {
  return 'term-' + (activeWorkspaceTerminals().length + 1);
}

async function createTerminalSession() {
  if (!activeWorkspaceId) {
    toast('请先新增工作区', 'error');
    return;
  }
  const defaultName = defaultTerminalSessionName();
  const input = window.prompt('终端名称', defaultName);
  if (input === null) return;
  const name = input.trim() || defaultName;
  if (typeof publishTerminalSnapshot === 'function') {
    await publishTerminalSnapshot(activeTerminalView(), { force: true });
  }
  try {
    const data = await backendApi.terminals.create({ workspace_id: activeWorkspaceId, name });
    if (!data.ok || !data.terminal) {
      toast(data.error || '终端创建失败', 'error');
      return;
    }
    mergeWorkspaceSummary(data.workspace);
    upsertTerminalSummary(data.terminal);
    setActiveTerminal(data.terminal.id || DEFAULT_TERMINAL);
    renderWorkspaceSwitch();
    renderTerminalTabs();
    showTerminalArea();
    if (termSocket && termSocket.connected) {
      attachTerminal(true);
    } else {
      connectTerminal();
    }
    toast('终端已创建', 'success');
  } catch(e) {
    toast('终端创建失败: ' + e.message, 'error');
  }
}

async function renameTerminalSession(terminalId) {
  if (!activeWorkspaceId || !terminalId) return;
  const terminal = activeWorkspaceTerminals().find(t => t.id === terminalId);
  if (!terminal) return;
  const threadId = String(terminal.thread_id || '').trim();
  const currentName = terminalLabel(terminal);
  const input = window.prompt(threadId ? '修改对话名称' : '修改终端名称', currentName);
  if (input === null) return;
  const name = input.trim();
  if (!name) {
    toast(threadId ? '对话名称不能为空' : '终端名称不能为空', 'error');
    return;
  }
  const maxLength = threadId ? 200 : 80;
  if (name.length > maxLength) {
    toast((threadId ? '对话名称' : '终端名称') + `不能超过 ${maxLength} 个字符`, 'error');
    return;
  }
  if (name === currentName) return;

  const workspaceId = activeWorkspaceId;
  try {
    if (threadId) {
      await updateCodexHistoryMetadata(threadId, { title: name });
      for (const workspace of workspaceList) {
        for (const item of workspace.terminals || []) {
          if (item.thread_id === threadId) item.thread_title = name;
        }
      }
      const conversation = codexHistoryById.get(threadId);
      if (conversation) conversation.title = name;
      renderWorkspaceSwitch();
      renderTerminalTabs();
      await loadCodexHistory();
      toast('对话名称已更新', 'success');
      return;
    }
    const data = await backendApi.terminals.rename(workspaceId, terminalId, name);
    if (!data.ok || !data.terminal) {
      toast(data.error || '终端重命名失败', 'error');
      return;
    }
    if (data.workspace) mergeWorkspaceSummary(data.workspace);
    upsertTerminalSummary(data.terminal);
    renderWorkspaceSwitch();
    if (workspaceId === activeWorkspaceId) renderTerminalTabs();
    toast('终端已重命名', 'success');
  } catch(e) {
    toast('终端重命名失败: ' + e.message, 'error');
  }
}

async function closeTerminalSession(terminalId) {
  if (!activeWorkspaceId || !terminalId) return;
  const terminal = activeWorkspaceTerminals().find(t => t.id === terminalId) || { id: terminalId, name: terminalId };
  if (!confirm('关闭终端 ' + terminalLabel(terminal) + '？运行中的进程会被停止。')) return;
  try {
    const data = await backendApi.terminals.close(activeWorkspaceId, terminalId);
    if (!data.ok) {
      toast(data.error || '终端关闭失败', 'error');
      return;
    }
    if (data.workspace) {
      mergeWorkspaceSummary(data.workspace);
    } else {
      const workspace = activeWorkspaceInfo();
      workspace.terminals = (workspace.terminals || []).filter(t => t.id !== terminalId);
    }
    if (typeof disposeTerminalView === 'function') disposeTerminalView(activeWorkspaceId, terminalId);
    const remaining = activeWorkspaceTerminals();
    renderWorkspaceSwitch();
    renderTerminalTabs();
    if (!remaining.length) {
      showNoTerminalState('没有终端');
    } else if (terminalId === activeTerminalId) {
      setActiveTerminal(remaining[0].id);
      switchTerminal(activeTerminalId);
    } else if (termFit) {
      resizeTerminalToContainer(80);
    }
    toast('终端已关闭', 'success');
  } catch(e) {
    toast('终端关闭失败: ' + e.message, 'error');
  }
}

async function closeWorkspace(workspaceId) {
  const workspace = workspaceList.find(s => s.id === workspaceId);
  if (!workspace) return;
  const name = workspace.name || workspace.id;
  const message = workspace.source === 'default'
    ? '关闭默认工作区 ' + name + '？不会删除磁盘目录，只会停止终端并从列表移除。'
    : '关闭工作区 ' + name + '？不会删除磁盘目录，只会停止终端并从列表移除。';
  if (!confirm(message)) return;
  try {
    const data = await backendApi.workspaces.close(workspaceId);
    if (!data.ok) {
      toast(data.error || '工作区关闭失败', 'error');
      return;
    }
    if (typeof disposeWorkspaceTerminalViews === 'function') disposeWorkspaceTerminalViews(workspaceId);
    workspaceList = data.workspaces || workspaceList.filter(s => s.id !== workspaceId);
    if (!workspaceList.some(s => s.id === activeWorkspaceId)) {
      activeWorkspaceId = workspaceList[0]?.id || '';
      if (activeWorkspaceId) {
        localStorage.setItem('active_workspace', activeWorkspaceId);
      } else {
        localStorage.removeItem('active_workspace');
      }
    }
    activeTerminalId = activeWorkspaceId ? (localStorage.getItem(terminalStorageKey(activeWorkspaceId)) || '') : '';
    normalizeActiveTerminal();
    workspaceCurrentDir = '';
    workspaceParentDir = null;
    workspaceCurrentFile = null;
    workspaceOriginalContent = '';
    workspaceModified = false;
    renderWorkspaceSwitch();
    renderTerminalTabs();
    if (activeSideMode === 'history') loadCodexHistory();
    if (activeSideMode === 'workspaceFiles') loadWorkspaceFiles('');
    if (!activeWorkspaceId || !activeWorkspaceTerminals().length) {
      showNoTerminalState(activeWorkspaceId ? '没有终端' : '没有工作区');
    } else if (termSocket && termSocket.connected) {
      attachTerminal(true);
    }
    toast(data.removed ? '工作区已关闭' : '工作区终端已关闭', 'success');
  } catch(e) {
    toast('工作区关闭失败: ' + e.message, 'error');
  }
}

async function loadWorkspaces() {
  try {
    const data = await backendApi.workspaces.list();
    workspaceList = data.workspaces || [];
    for (const item of workspaceList) {
      if (item.observation) mergeWorkspaceObservation(item.id, item.observation);
    }
    if (!workspaceList.some(s => s.id === activeWorkspaceId)) {
      activeWorkspaceId = workspaceList[0]?.id || '';
    }
    if (activeWorkspaceId) {
      localStorage.setItem('active_workspace', activeWorkspaceId);
    } else {
      localStorage.removeItem('active_workspace');
    }
    normalizeActiveTerminal();
    renderWorkspaceSwitch();
    renderTerminalTabs();
  } catch(e) {
    workspaceList = [];
    activeWorkspaceId = '';
    activeTerminalId = '';
    renderWorkspaceSwitch();
    renderTerminalTabs();
  }
}

function renderWorkspaceSwitch() {
  const el = document.getElementById('workspaceSwitch');
  if (!workspaceList.length) {
    el.innerHTML = '<div class="empty-workspaces">尚未添加工作区</div>';
    return;
  }
  el.innerHTML = workspaceList.map(s => {
    const active = s.id === activeWorkspaceId ? ' active' : '';
    const title = s.pty_port ? `${s.cwd} · ${s.pty_host}:${s.pty_port}` : s.cwd;
    const missing = s.exists === false ? ' (missing)' : '';
    const terminal = primaryTerminalForWorkspace(s);
    const state = workspaceVisualState(s);
    const pid = terminal.pid ? ` · pid ${terminal.pid}` : '';
    const activity = terminalActivityText(terminal);
    const stateText = state === 'attention'
      ? 'Codex 已完成，点击查看'
      : (state === 'busy' ? (activity || 'Working') : (state === 'missing' ? '目录缺失' : '空闲'));
    return `<button class="workspace-btn state-${state}${active}" data-workspace-id="${escapeHtml(s.id)}" title="${escapeHtml((title || '') + pid + ' · ' + stateText)}">
      <span class="workspace-dot ${state}"></span>
      <span class="workspace-name">${escapeHtml((s.name || s.id) + missing)}</span>
      <span role="button" tabindex="0" data-pin-workspace="${escapeHtml(s.id)}" aria-label="${s.pinned ? '取消置顶' : '置顶'}">${s.pinned ? '★' : '☆'}</span>
      <span class="tab-close workspace-close" role="button" tabindex="0" data-close-workspace="${escapeHtml(s.id)}" title="关闭工作区" aria-label="关闭工作区">${CLOSE_ICON_SVG}</span>
    </button>`;
  }).join('');
}

function activeWorkspaceInfo() {
  return workspaceList.find(s => s.id === activeWorkspaceId) || { id: activeWorkspaceId, name: activeWorkspaceId || '未选择工作区' };
}

function activeWorkspaceLabel() {
  const info = activeWorkspaceInfo();
  return info.name || info.id || activeWorkspaceId;
}

document.getElementById('workspaceSwitch').addEventListener('click', e => {
  if (suppressWorkspaceClick) {
    e.preventDefault();
    e.stopPropagation();
    suppressWorkspaceClick = false;
    return;
  }
  const pin = e.target.closest('[data-pin-workspace]');
  if (pin) { e.preventDefault(); e.stopPropagation(); toggleWorkspacePin(pin.dataset.pinWorkspace); return; }
  const close = e.target.closest('[data-close-workspace]');
  if (close) {
    e.preventDefault();
    e.stopPropagation();
    closeWorkspace(close.dataset.closeWorkspace);
    return;
  }
  const btn = e.target.closest('.workspace-btn');
  if (!btn) return;
  const workspaceId = btn.dataset.workspaceId;
  const workspace = workspaceList.find(item => item.id === workspaceId);
  if (workspaceHasUnreadCodexCompletion(workspace)) {
    markCodexCompletionSeen(workspaceId);
  }
  if (workspaceId !== activeWorkspaceId) {
    switchWorkspace(workspaceId);
  }
});

document.getElementById('workspaceSwitch').addEventListener('pointerdown', e => {
  if (e.button !== undefined && e.button !== 0) return;
  const btn = e.target.closest('.workspace-btn');
  if (!btn || e.target.closest('[data-close-workspace], [data-pin-workspace]')) {
    return;
  }
  draggingWorkspaceId = btn.dataset.workspaceId || '';
  if (!draggingWorkspaceId) {
    return;
  }
  workspaceDragPointerId = e.pointerId;
  workspaceDragStartX = e.clientX;
  workspaceDragStartY = e.clientY;
  workspaceDragActive = false;
  try {
    btn.setPointerCapture(e.pointerId);
  } catch(_) {}
});

document.getElementById('workspaceSwitch').addEventListener('pointermove', e => {
  if (!draggingWorkspaceId || e.pointerId !== workspaceDragPointerId) return;
  const dx = e.clientX - workspaceDragStartX;
  const dy = e.clientY - workspaceDragStartY;
  if (!workspaceDragActive) {
    if (Math.hypot(dx, dy) < 5) return;
    workspaceDragActive = true;
    const dragging = document.querySelector(`.workspace-btn[data-workspace-id="${cssEscape(draggingWorkspaceId)}"]`);
    dragging?.classList.add('dragging');
  }
  e.preventDefault();
  scrollWorkspaceSwitchDuringDrag(e.clientX);
  const dragging = document.querySelector(`.workspace-btn[data-workspace-id="${cssEscape(draggingWorkspaceId)}"]`);
  const target = document.elementFromPoint(e.clientX, e.clientY)?.closest('.workspace-btn');
  if (!dragging || !target || dragging === target) return;
  if (target.parentNode !== dragging.parentNode) return;
  const rect = target.getBoundingClientRect();
  const afterTarget = e.clientX > rect.left + rect.width / 2;
  target.parentNode.insertBefore(dragging, afterTarget ? target.nextSibling : target);
});

document.getElementById('workspaceSwitch').addEventListener('pointerup', e => {
  if (!draggingWorkspaceId || e.pointerId !== workspaceDragPointerId) return;
  const shouldSave = workspaceDragActive;
  if (shouldSave) {
    e.preventDefault();
    suppressWorkspaceClick = true;
  }
  finishWorkspacePointerDrag(e);
  if (shouldSave) saveWorkspaceOrderFromDom();
});

document.getElementById('workspaceSwitch').addEventListener('pointercancel', e => {
  if (e.pointerId !== workspaceDragPointerId) return;
  finishWorkspacePointerDrag(e);
});

function finishWorkspacePointerDrag(event) {
  const dragging = draggingWorkspaceId
    ? document.querySelector(`.workspace-btn[data-workspace-id="${cssEscape(draggingWorkspaceId)}"]`)
    : null;
  try {
    dragging?.releasePointerCapture(event.pointerId);
  } catch(_) {}
  document.querySelectorAll('.workspace-btn.dragging').forEach(btn => btn.classList.remove('dragging'));
  draggingWorkspaceId = '';
  workspaceDragPointerId = null;
  workspaceDragStartX = 0;
  workspaceDragStartY = 0;
  workspaceDragActive = false;
  setTimeout(() => { suppressWorkspaceClick = false; }, 200);
}

function scrollWorkspaceSwitchDuringDrag(clientX) {
  const el = document.getElementById('workspaceSwitch');
  if (!el) return;
  const rect = el.getBoundingClientRect();
  const edge = 34;
  if (clientX < rect.left + edge) {
    el.scrollLeft -= 12;
  } else if (clientX > rect.right - edge) {
    el.scrollLeft += 12;
  }
}

function cssEscape(value) {
  if (window.CSS && typeof window.CSS.escape === 'function') {
    return window.CSS.escape(String(value));
  }
  return String(value).replace(/["\\]/g, '\\$&');
}

function workspaceOrderFromDom() {
  return Array.from(document.querySelectorAll('#workspaceSwitch .workspace-btn'))
    .map(btn => btn.dataset.workspaceId)
    .filter(Boolean);
}

function applyWorkspaceOrder(order) {
  const byId = new Map(workspaceList.map(item => [item.id, item]));
  const ordered = [];
  const seen = new Set();
  for (const id of order) {
    const item = byId.get(id);
    if (!item || seen.has(id)) continue;
    ordered.push(item);
    seen.add(id);
  }
  for (const item of workspaceList) {
    if (!seen.has(item.id)) ordered.push(item);
  }
  workspaceList = ordered;
}

async function saveWorkspaceOrderFromDom() {
  const order = workspaceOrderFromDom();
  if (order.length < 2) return;
  const previous = workspaceList.slice();
  applyWorkspaceOrder(order);
  renderWorkspaceSwitch();
  try {
    const data = await backendApi.workspaces.reorder(order);
    workspaceList = data.workspaces || workspaceList;
    renderWorkspaceSwitch();
    renderTerminalTabs();
  } catch(e) {
    workspaceList = previous;
    renderWorkspaceSwitch();
    toast('工作区排序保存失败: ' + e.message, 'error');
  }
}

document.getElementById('terminalTabs').addEventListener('click', e => {
  const close = e.target.closest('[data-close-terminal]');
  if (close) {
    e.preventDefault();
    e.stopPropagation();
    closeTerminalSession(close.dataset.closeTerminal);
    return;
  }
  const btn = e.target.closest('.terminal-tab');
  if (!btn) return;
  const terminalId = btn.dataset.terminalId;
  const terminal = activeWorkspaceTerminals().find(item => item.id === terminalId);
  if (terminalHasUnreadCodexCompletion(terminal, activeWorkspaceId)) {
    markCodexCompletionSeen(activeWorkspaceId, terminalId);
  }
  const editorOpen = document.getElementById('workspaceEditorArea')?.classList.contains('active');
  if (editorOpen) {
    switchTerminal(terminalId);
    return;
  }
  if (terminalId === activeTerminalId) {
    renameTerminalSession(terminalId);
    return;
  }
  switchTerminal(terminalId);
});

function switchWorkspace(workspaceId) {
  if (workspaceId === activeWorkspaceId) return;
  if (workspaceModified && !confirm('当前工作区文件未保存，是否继续？')) return;
  if (typeof publishTerminalSnapshot === 'function') {
    publishTerminalSnapshot(activeTerminalView(), { force: true }).catch(() => false);
  }
  const switchSeq = ++workspaceSwitchSeq;
  activeWorkspaceId = workspaceId;
  localStorage.setItem('active_workspace', activeWorkspaceId);
  activeTerminalId = localStorage.getItem(terminalStorageKey(activeWorkspaceId)) || DEFAULT_TERMINAL;
  normalizeActiveTerminal();
  renderWorkspaceSwitch();
  renderTerminalTabs();
  showTerminalArea();
  const nextView = typeof activateTerminalView === 'function' ? activateTerminalView() : null;
  termConnected = false;
  setTermStatus(false, '连接 ' + activeWorkspaceLabel() + ' / ' + terminalLabel() + ' ...');
  if (termSocket && termSocket.connected) {
    attachTerminal(true);
  } else {
    connectTerminal();
  }
  try { nextView?.term?.focus(); } catch(e) {}
  const terminalBeforeSync = activeTerminalId;
  syncWorkspaceTerminals(workspaceId, { render: false }).then(() => {
    if (switchSeq !== workspaceSwitchSeq || workspaceId !== activeWorkspaceId) return;
    normalizeActiveTerminal();
    renderWorkspaceSwitch();
    renderTerminalTabs();
    if (!activeTerminalId || !activeWorkspaceTerminals().length) {
      showNoTerminalState('没有终端');
      return;
    }
    if (typeof activateTerminalView === 'function') activateTerminalView();
    if (termSocket && termSocket.connected) {
      if (terminalBeforeSync !== activeTerminalId || !termConnected) attachTerminal(true);
    } else {
      connectTerminal();
    }
  });
  codexHistoryLoaded = true;
  loadCodexHistory();
  workspaceCurrentDir = '';
  workspaceParentDir = null;
  workspaceCurrentFile = null;
  workspaceOriginalContent = '';
  workspaceModified = false;
  if (activeSideMode === 'workspaceFiles') {
    document.getElementById('sidebarTitle').textContent = activeWorkspaceLabel() + ' Files';
  }
  if (activeSideMode === 'workspaceFiles') loadWorkspaceFiles('');
}

function workspaceLabelById(workspaceId) {
  const info = workspaceList.find(s => s.id === workspaceId);
  return (info && (info.name || info.id)) || workspaceId || '未选择工作区';
}

async function fetchWorkspaceObservations(workspaceId = activeWorkspaceId) {
  if (!workspaceId) return workspaceObservation(workspaceId);
  try {
    const data = await backendApi.observations.get(workspaceId);
    if (data.workspace) mergeWorkspaceObservation(workspaceId, data.workspace);
  } catch(e) {
    console.warn('load observations failed', e);
  }
  return workspaceObservation(workspaceId);
}

function renderObservationEvents(events) {
  if (!events || !events.length) {
    return '<div class="observation-line">暂无 hook 事件。</div>';
  }
  return events.map(event => {
    const status = event.status || 'done';
    const label = status === 'failed' ? '失败' : (status === 'running' ? '运行中' : '完成');
    const title = event.title || 'hook';
    const message = event.message || '';
    const time = formatTime(event.timestamp);
    return `<div class="observation-event ${escapeHtml(status)}">
      <div class="observation-line">${escapeHtml(label + ' · ' + title + (time ? ' · ' + time : ''))}</div>
      ${message ? `<div class="observation-line">${escapeHtml(message)}</div>` : ''}
      <div class="observation-line">terminal: ${escapeHtml(event.terminal_id || DEFAULT_TERMINAL)} · source: ${escapeHtml(event.source || '')}</div>
    </div>`;
  }).join('');
}

function renderWorkspaceObservations(workspaceId = activeWorkspaceId) {
  const observation = workspaceObservation(workspaceId);
  document.getElementById('observationTitle').textContent = workspaceLabelById(workspaceId) + ' 状态';
  const terminals = observation.terminals || [];
  let html = `<div class="observation-card">
    <h4>最近 hook</h4>
    ${renderObservationEvents(observation.recent_hook_events || [])}
  </div>`;
  if (terminals.length) {
    html += terminals.map(termObs => `<div class="observation-card">
      <h4>终端 ${escapeHtml(termObs.terminal_id || DEFAULT_TERMINAL)}</h4>
      ${renderObservationEvents(termObs.hook_events || [])}
    </div>`).join('');
  }
  document.getElementById('observationBody').innerHTML = html;
}

async function showWorkspaceObservations(workspaceId = activeWorkspaceId) {
  if (!workspaceId) return;
  await fetchWorkspaceObservations(workspaceId);
  renderWorkspaceObservations(workspaceId);
  document.getElementById('observationModal').style.display = 'flex';
  await markWorkspaceObservationsRead(workspaceId);
}

function hideWorkspaceObservations() {
  document.getElementById('observationModal').style.display = 'none';
}

async function markWorkspaceObservationsRead(workspaceId = activeWorkspaceId) {
  if (!workspaceId) return;
  try {
    await backendApi.observations.markRead(workspaceId);
    const observation = workspaceObservation(workspaceId);
    observation.unread_hooks = 0;
    for (const terminal of observation.terminals || []) {
      terminal.unread_hooks = 0;
    }
    mergeWorkspaceObservation(workspaceId, observation);
    renderWorkspaceSwitch();
    renderTerminalTabs();
  } catch(e) {
    console.warn('mark observations read failed', e);
  }
}

function showWorkspaceModal() {
  document.getElementById('workspaceModal').style.display = 'flex';
  document.getElementById('workspaceName').value = '';
  setWorkspaceMode('import');
  browseWorkspacePath(document.getElementById('workspacePath').value || '~');
  document.getElementById('workspacePath').focus();
}

function hideWorkspaceModal() {
  document.getElementById('workspaceModal').style.display = 'none';
}

function setWorkspaceMode(mode) {
  workspaceMode = mode === 'create' ? 'create' : 'import';
  document.querySelectorAll('.modal-tab[data-workspace-mode]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.workspaceMode === workspaceMode);
  });
  document.getElementById('workspaceBrowser').style.display = workspaceMode === 'import' ? 'block' : 'none';
  document.getElementById('workspaceSubmitBtn').textContent = workspaceMode === 'create' ? '创建并打开' : '导入并打开';
  document.getElementById('workspacePath').placeholder = workspaceMode === 'create'
    ? '新目录路径，例如 /home/t/work/my-topic'
    : '已有目录路径，例如 /home/t/project';
}

async function browseWorkspacePath(path) {
  if (workspaceMode !== 'import') return;
  const listEl = document.getElementById('workspaceBrowserList');
  listEl.innerHTML = '<div class="empty-workspaces">加载中...</div>';
  try {
    const data = await backendApi.system.directories(path || '~');
    workspaceBrowserPath = data.path || '';
    workspaceBrowserParent = data.parent || null;
    document.getElementById('workspacePath').value = workspaceBrowserPath;
    document.getElementById('workspaceBrowserPath').textContent = workspaceBrowserPath;
    renderWorkspaceBrowser(data.dirs || []);
  } catch(e) {
    listEl.innerHTML = '<div class="empty-workspaces">无法读取该目录</div>';
  }
}

function browseWorkspaceParent() {
  if (workspaceBrowserParent) browseWorkspacePath(workspaceBrowserParent);
}

function renderWorkspaceBrowser(dirs) {
  const listEl = document.getElementById('workspaceBrowserList');
  let html = `<button class="path-browser-item" type="button" data-select-current="1">选择当前目录</button>`;
  for (const dir of dirs) {
    html += `<button class="path-browser-item" type="button" data-path="${escapeHtml(dir.path)}">
      <span>📁</span><span>${escapeHtml(dir.name)}</span>
    </button>`;
  }
  if (!dirs.length) {
    html += '<div class="empty-workspaces">没有可进入的子目录</div>';
  }
  listEl.innerHTML = html;
}

document.getElementById('workspaceBrowserList').addEventListener('click', e => {
  const current = e.target.closest('[data-select-current]');
  if (current) {
    document.getElementById('workspacePath').value = workspaceBrowserPath;
    return;
  }
  const item = e.target.closest('[data-path]');
  if (item) browseWorkspacePath(item.dataset.path);
});

async function registerWorkspace() {
  const name = document.getElementById('workspaceName').value.trim();
  const path = document.getElementById('workspacePath').value.trim();
  if (!path) {
    toast('请输入目录路径', 'error');
    return;
  }
  try {
    const data = await backendApi.workspaces.register({
      name,
      path,
      create: workspaceMode === 'create',
    });
    if (!data.ok || !data.workspace) {
      toast(data.error || '注册失败', 'error');
      return;
    }
    hideWorkspaceModal();
    await loadWorkspaces();
    if (activeWorkspaceId === data.workspace.id) {
      mergeWorkspaceSummary(data.workspace);
      normalizeActiveTerminal();
      renderWorkspaceSwitch();
      renderTerminalTabs();
      loadCodexHistory();
      if (activeSideMode === 'workspaceFiles') loadWorkspaceFiles('');
      if (termSocket && termSocket.connected) {
        attachTerminal(true);
      } else {
        connectTerminal();
      }
    } else {
      await switchWorkspace(data.workspace.id);
    }
    if (data.existing) {
      toast('工作区已存在，已打开', 'success');
    } else if (workspaceMode === 'create') {
      toast('目录已创建，工作区已打开', 'success');
    } else {
      toast('工作区已导入', 'success');
    }
  } catch(e) {
    toast('注册失败: ' + e.message, 'error');
  }
}

function applySystemStats(data) {
  document.getElementById('cpuStat').textContent = formatPercent(data.cpu_percent);
  document.getElementById('memStat').textContent = formatMemory(data.memory?.used) + ' / ' + formatMemory(data.memory?.total);
  document.getElementById('procStat').textContent = formatMemory(data.process?.rss);
  const terminalCounts = data.terminal_counts || {};
  const terminalTotal = Number(terminalCounts.total ?? Object.keys(data.terminals || {}).length);
  const terminalActive = Number(terminalCounts.active ?? Object.values(data.terminals || {}).filter(item => item?.alive && item?.usable).length);
  const countLabel = document.getElementById('terminalCountLabel');
  if (countLabel) countLabel.textContent = terminalTotal + ' 总 / ' + terminalActive + ' 活跃';
  if (data.observations_by_workspace) {
    applyWorkspaceObservations(data.observations_by_workspace);
  }
  const grouped = terminalStatsByWorkspace(data);
  if (!grouped) return;
  const signature = terminalStatsStructureSignature(grouped);
  const structureChanged = signature !== statsTerminalSignature;
  statsTerminalSignature = signature;
  for (const item of workspaceList) {
    const terminals = grouped[item.id] || [];
    item.terminals = terminals;
    item.terminal = terminals.find(t => t.id === DEFAULT_TERMINAL) || terminals[0] || null;
  }
  acknowledgeDisplayedCodexCompletion();
  if (structureChanged) {
    renderWorkspaceSwitch();
    renderTerminalTabs();
  } else {
    updateRuntimeStatusIndicators();
  }
}

function terminalStatsByWorkspace(data) {
  if (data.terminals_by_workspace) return data.terminals_by_workspace;
  if (!data.terminals) return null;
  const grouped = {};
  for (const [key, terminal] of Object.entries(data.terminals)) {
    const workspaceId = terminal?.workspace_id || String(key).split(':', 1)[0];
    if (!workspaceId || !terminal) continue;
    (grouped[workspaceId] ||= []).push(terminal);
  }
  return grouped;
}

function terminalStatsStructureSignature(grouped) {
  return JSON.stringify(workspaceList.map(workspace => [
    workspace.id,
    (grouped[workspace.id] || []).map(terminal => [
      terminal.id || DEFAULT_TERMINAL,
      terminal.name || terminal.id || DEFAULT_TERMINAL,
      terminal.thread_id || '',
      terminal.thread_title || '',
    ]),
  ]));
}

function updateRuntimeStatusIndicators() {
  for (const workspace of workspaceList) {
    const workspaceButton = document.querySelector(
      `.workspace-btn[data-workspace-id="${cssEscape(workspace.id)}"]`
    );
    if (!workspaceButton) continue;
    const state = workspaceVisualState(workspace);
    workspaceButton.classList.remove('state-busy', 'state-idle', 'state-missing', 'state-attention');
    workspaceButton.classList.add('state-' + state);
    const dot = workspaceButton.querySelector('.workspace-dot');
    if (dot) dot.className = 'workspace-dot ' + state;
    const terminal = primaryTerminalForWorkspace(workspace);
    const activity = terminalActivityText(terminal);
    const stateText = state === 'attention'
      ? 'Codex 已完成，点击查看'
      : (state === 'busy' ? (activity || 'Working') : (state === 'missing' ? '目录缺失' : '空闲'));
    const location = workspace.pty_port
      ? `${workspace.cwd} · ${workspace.pty_host}:${workspace.pty_port}`
      : workspace.cwd;
    const pid = terminal.pid ? ` · pid ${terminal.pid}` : '';
    workspaceButton.title = (location || '') + pid + ' · ' + stateText;
  }

  for (const terminal of activeWorkspaceTerminals()) {
    const terminalButton = document.querySelector(
      `.terminal-tab[data-terminal-id="${cssEscape(terminal.id)}"]`
    );
    if (!terminalButton) continue;
    const state = terminalVisualState(terminal, activeWorkspaceId);
    terminalButton.classList.remove('state-busy', 'state-idle', 'state-shell', 'state-dead', 'state-attention');
    terminalButton.classList.add('state-' + state);
    const dot = terminalButton.querySelector('.workspace-dot');
    if (dot) dot.className = 'workspace-dot ' + state;
    const pid = terminal.pid ? ` · pid ${terminal.pid}` : '';
    const activity = terminalActivityText(terminal);
    const renameHint = terminal.id === activeTerminalId ? ' · 再次点击可重命名' : '';
    const attention = state === 'attention' ? ' · 已完成，点击查看' : '';
    terminalButton.title = `${activeWorkspaceLabel()} / ${terminalLabel(terminal)}${pid}${activity ? ' · ' + activity : ''}${attention}${renameHint}`;
  }
}

async function refreshStats() {
  if (statsRefreshInFlight) return null;
  statsRefreshInFlight = true;
  try {
    const data = await backendApi.system.stats();
    applySystemStats(data);
    updateBigScreenFromStats(data);
    return data;
  } catch(e) {
    stopStatsRefresh();
    return null;
  } finally {
    statsRefreshInFlight = false;
  }
}

function startStatsRefresh() {
  if (statsTimer) return;
  refreshStats();
  statsTimer = setInterval(refreshStats, STATS_REFRESH_MS);
}

function stopStatsRefresh() {
  if (statsTimer) clearInterval(statsTimer);
  statsTimer = null;
}

function formatPercent(value) {
  return value === null || value === undefined ? '--' : Number(value).toFixed(1) + '%';
}

function formatMemory(bytes) {
  if (!bytes) return '--';
  if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(0) + 'M';
  return (bytes / 1024 / 1024 / 1024).toFixed(1) + 'G';
}

function escapeHtml(value) {
  return String(value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
