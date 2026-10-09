// ===================== Codex History =====================

let codexResumeInFlight = false;

async function loadCodexHistory() {
  const listEl = document.getElementById('codexHistoryList');
  syncArchivedHistoryToggle();
  if (!activeWorkspaceId) {
    listEl.innerHTML = '<div class="codex-history-empty">请先新增工作区</div>';
    return;
  }
  const workspaceId = activeWorkspaceId;
  const requestSeq = ++codexHistoryRequestSeq;
  listEl.innerHTML = '<div class="codex-history-empty">加载中...</div>';
  try {
    const data = {conversations: []};
    let offset = 0;
    do {
      const page = await backendApi.conversations.list(workspaceId, codexShowArchived, offset);
      if (requestSeq !== codexHistoryRequestSeq || workspaceId !== activeWorkspaceId) return;
      data.conversations.push(...(page.conversations || []));
      offset = page.nextOffset;
    } while (Number.isInteger(offset) && offset > 0);
    if (requestSeq !== codexHistoryRequestSeq || workspaceId !== activeWorkspaceId) return;
    codexHistoryItems = data.conversations || [];
    codexHistoryById = new Map(codexHistoryItems.map(conv => [conv.threadId, conv]));
    renderCodexHistory(codexHistoryItems);
    renderTerminalTabs();
  } catch(e) {
    if (requestSeq !== codexHistoryRequestSeq || workspaceId !== activeWorkspaceId) return;
    listEl.innerHTML = '<div class="codex-history-empty">加载失败</div>';
  }
}

function renderCodexHistory(conversations) {
  const listEl = document.getElementById('codexHistoryList');
  if (!conversations.length) {
    listEl.innerHTML = '<div class="codex-history-empty">暂无 Codex 对话记录</div>';
    return;
  }
  let html = '';
  for (const conv of conversations) {
    const ts = new Date(conv.timestamp * 1000);
    const timeStr = ts.toLocaleDateString() + ' ' + ts.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
    const escapedTitle = escapeHtml(conv.title || conv.rawTitle || 'Untitled');
    const threadId = escapeHtml(conv.threadId);
    const shortThreadId = escapeHtml(String(conv.threadId || '').slice(0, 8));
    const forkedFromId = String(conv.forkedFromId || '');
    const forkLabel = forkedFromId ? ' · 继承自 ' + forkedFromId.slice(0, 8) : '';
    const archived = conv.archived ? ' · 已归档' : '';
    const starLabel = conv.important ? '取消重要' : '标记重要';
    const starClass = 'codex-history-star' + (conv.important ? ' active' : '');
    const itemClass = 'codex-history-item'
      + (conv.important ? ' important' : '')
      + (conv.archived ? ' archived' : '');
    html += `<div class="${itemClass}" data-codex-thread-id="${threadId}">
      <button class="${starClass}" type="button" data-history-action="important" data-thread-id="${threadId}" title="${starLabel}" aria-label="${starLabel}">★</button>
      <div class="ch-row">
        <span class="ch-title" title="${escapedTitle}">${escapedTitle}</span>
      </div>
      <span class="ch-meta" title="${threadId}${forkedFromId ? ' ← ' + escapeHtml(forkedFromId) : ''}">${escapeHtml(timeStr + ' · ID ')}${shortThreadId}${escapeHtml(forkLabel + archived)}</span>
      <div class="codex-history-actions">
        <button class="codex-history-action" type="button" data-history-action="preview" data-thread-id="${threadId}">查看</button>
        <button class="codex-history-action" type="button" data-history-action="rename" data-thread-id="${threadId}">命名</button>
        <button class="codex-history-action" type="button" data-history-action="archive" data-thread-id="${threadId}">${conv.archived ? '恢复' : '归档'}</button>
      </div>
    </div>`;
  }
  listEl.innerHTML = html;
}

async function updateCodexHistoryMetadata(threadId, updates) {
  return backendApi.conversations.update(threadId, updates);
}

async function handleCodexHistoryAction(action, threadId) {
  const conv = codexHistoryById.get(threadId);
  if (!conv) return;
  try {
    if (action === 'preview') {
      await previewConversation(threadId);
      return;
    } else if (action === 'rename') {
      const input = window.prompt('对话名称', conv.title || conv.rawTitle || '');
      if (input === null) return;
      await updateCodexHistoryMetadata(threadId, { title: input.trim() || null });
      toast('名称已更新', 'success');
    } else if (action === 'important') {
      await updateCodexHistoryMetadata(threadId, { important: !conv.important });
      toast(conv.important ? '已取消重要' : '已标记重要', 'success');
    } else if (action === 'archive') {
      await updateCodexHistoryMetadata(threadId, { archived: !conv.archived });
      toast(conv.archived ? '已恢复' : '已归档', 'success');
    }
    await loadCodexHistory();
  } catch(e) {
    toast('History 更新失败: ' + e.message, 'error');
  }
}

function codexConversationTerminalName(conv) {
  const custom = String(conv?.customTitle || '').trim();
  if (custom) return custom;
  return Array.from(String(conv?.rawTitle || '').trim()).slice(0, 4).join('');
}

async function createHistoryResumeTerminal(conv) {
  if (!activeWorkspaceId) {
    throw new Error('没有可用工作区');
  }

  const name = codexConversationTerminalName(conv) || defaultTerminalSessionName();
  if (typeof publishTerminalSnapshot === 'function') {
    await publishTerminalSnapshot(activeTerminalView(), { force: true });
  }
  const data = await backendApi.terminals.resume({
    workspace_id: activeWorkspaceId,
    name,
    thread_id: conv.threadId,
  });
  if (!data.ok || !data.terminal) {
    throw new Error(data.error || '对话终端打开失败');
  }

  if (data.workspace) mergeWorkspaceSummary(data.workspace);
  upsertTerminalSummary(data.terminal);
  setActiveTerminal(data.terminal.id || DEFAULT_TERMINAL);
  termConnected = false;
  renderWorkspaceSwitch();
  renderTerminalTabs();
  showTerminalArea();
  if (termSocket && termSocket.connected) {
    attachTerminal(true);
  } else {
    connectTerminal();
  }
  return {
    terminal: data.terminal,
    created: Boolean(data.created),
    resumeStarted: Boolean(data.resume_started),
  };
}

async function resumeCodexThread(threadId) {
  if (codexResumeInFlight) return;
  codexResumeInFlight = true;
  try {
    const conv = codexHistoryById.get(threadId) || { threadId };
    showTerminalArea();
    initTerminal();
    const result = await createHistoryResumeTerminal(conv);
    if (result.resumeStarted && typeof queueTerminalHistoryResumeReconcile === 'function') {
      queueTerminalHistoryResumeReconcile(activeTerminalView());
    }
    term?.focus();
    if (result.resumeStarted) {
      toast(result.created ? '已在新终端继续会话' : '已在原终端继续会话', 'success');
    } else {
      toast('已切换到该对话终端', 'success');
    }
  } catch(e) {
    toast('打开对话失败: ' + e.message, 'error');
  } finally {
    codexResumeInFlight = false;
  }
}

function jsString(value) {
  return JSON.stringify(String(value));
}
