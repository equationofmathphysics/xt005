// ===================== Workspace Files =====================

document.querySelector('.sidebar-mode-tabs').addEventListener('click', e => {
  const btn = e.target.closest('.side-mode');
  if (!btn) return;
  const mode = btn.dataset.sideMode;
  if (mode === 'history') {
    if (activeSideMode === 'history') {
      toggleArchivedHistory();
      return;
    }
    if (codexShowArchived) {
      codexShowArchived = false;
      localStorage.setItem('codex_history_show_archived', '0');
    }
  }
  switchSideMode(mode);
});

document.getElementById('workspaceFileList').addEventListener('click', e => {
  const download = e.target.closest('.file-action');
  if (download) {
    e.stopPropagation();
    downloadWorkspaceFile(download.dataset.download);
    return;
  }
  const item = e.target.closest('.file-item');
  if (!item) return;
  const path = item.dataset.path || '';
  if (item.dataset.action === 'dir') {
    loadWorkspaceFiles(path);
  } else if (item.dataset.action === 'file') {
    openWorkspaceFile(path);
  }
});

document.getElementById('codexHistoryList').addEventListener('click', e => {
  const action = e.target.closest('[data-history-action]');
  if (action) {
    e.preventDefault();
    e.stopPropagation();
    handleCodexHistoryAction(action.dataset.historyAction, action.dataset.threadId);
    return;
  }
  const item = e.target.closest('[data-codex-thread-id]');
  if (!item) return;
  e.preventDefault();
  e.stopPropagation();
  resumeCodexThread(item.dataset.codexThreadId);
});

document.getElementById('workspaceUploadInput').addEventListener('change', uploadWorkspaceFileFromInput);

document.getElementById('workspaceEditor').addEventListener('input', () => {
  workspaceModified = document.getElementById('workspaceEditor').value !== workspaceOriginalContent;
  updateWorkspaceChars();
});

document.getElementById('workspaceEditor').addEventListener('keydown', e => {
  if (e.key === 'Tab') {
    e.preventDefault();
    const ta = e.target;
    const s = ta.selectionStart, end = ta.selectionEnd;
    ta.value = ta.value.substring(0, s) + '    ' + ta.value.substring(end);
    ta.selectionStart = ta.selectionEnd = s + 4;
    workspaceModified = ta.value !== workspaceOriginalContent;
    updateWorkspaceChars();
  }
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault();
    saveWorkspaceFile();
  }
});

function syncArchivedHistoryToggle() {
  const btn = document.querySelector('.side-mode[data-side-mode="history"]');
  if (!btn) return;
  const showingArchived = activeSideMode === 'history' && codexShowArchived;
  btn.textContent = showingArchived ? 'Archived' : 'History';
  btn.title = showingArchived ? '点击返回普通 History' : '再次点击查看 Archived History';
  btn.setAttribute('aria-label', btn.title);
}

function toggleArchivedHistory() {
  codexShowArchived = !codexShowArchived;
  localStorage.setItem('codex_history_show_archived', codexShowArchived ? '1' : '0');
  document.getElementById('sidebarTitle').textContent = codexShowArchived ? 'Archived History' : 'Codex History';
  syncArchivedHistoryToggle();
  loadCodexHistory();
}

function restoreCodexSidebarState() {
  const sidebar = document.getElementById('codexSidebar');
  localStorage.removeItem(SIDEBAR_COLLAPSED_KEY);
  if (sidebar) sidebar.classList.remove('collapsed');
}

function switchSideMode(mode) {
  activeSideMode = mode;
  document.querySelectorAll('.side-mode').forEach(btn => btn.classList.toggle('active', btn.dataset.sideMode === mode));
  document.getElementById('historyPane').classList.toggle('active', mode === 'history');
  document.getElementById('workspaceFilesPane').classList.toggle('active', mode === 'workspaceFiles');
  document.getElementById('sidebarTitle').textContent = mode === 'history'
    ? (codexShowArchived ? 'Archived History' : 'Codex History')
    : activeWorkspaceLabel() + ' Files';
  syncArchivedHistoryToggle();
  if (mode === 'history') {
    loadCodexHistory();
  } else {
    loadWorkspaceFiles(workspaceCurrentDir || '');
  }
}

function refreshSidePane() {
  if (activeSideMode === 'history') {
    loadCodexHistory();
  } else {
    loadWorkspaceFiles(workspaceCurrentDir || '');
  }
}

function triggerWorkspaceUpload() {
  if (!activeWorkspaceId) {
    toast('请先新增工作区', 'error');
    return;
  }
  const input = document.getElementById('workspaceUploadInput');
  input.value = '';
  input.click();
}

async function uploadWorkspaceFileFromInput(e) {
  if (!activeWorkspaceId) {
    toast('请先新增工作区', 'error');
    return;
  }
  const input = e.target;
  const file = input.files && input.files[0];
  if (!file) return;

  try {
    const data = await backendApi.files.upload(activeWorkspaceId, '', file);
    if (!data.ok) {
      toast(data.error || '上传失败', 'error');
      return;
    }
    toast('已上传: ' + data.name, 'success');
    loadWorkspaceFiles('');
    if (workspaceCurrentFile === data.path) openWorkspaceFile(data.path);
  } catch(err) {
    toast('上传失败', 'error');
  } finally {
    input.value = '';
  }
}

async function loadWorkspaceFiles(subDir = workspaceCurrentDir) {
  const listEl = document.getElementById('workspaceFileList');
  if (!activeWorkspaceId) {
    listEl.innerHTML = '<div class="codex-history-empty">请先新增工作区</div>';
    return;
  }
  const workspaceId = activeWorkspaceId;
  const requestSeq = ++workspaceFileListRequestSeq;
  listEl.innerHTML = '<div class="codex-history-empty">加载中...</div>';
  try {
    const data = await backendApi.files.list(workspaceId, subDir || '');
    if (requestSeq !== workspaceFileListRequestSeq || workspaceId !== activeWorkspaceId) return;
    workspaceCurrentDir = data.current_dir || '';
    workspaceParentDir = data.parent_dir ?? null;
    renderWorkspaceFileList(data.files || [], workspaceCurrentDir, workspaceParentDir);
  } catch(e) {
    if (requestSeq !== workspaceFileListRequestSeq || workspaceId !== activeWorkspaceId) return;
    listEl.innerHTML = '<div class="codex-history-empty">加载失败</div>';
  }
}

async function openWorkspaceFolder() {
  if (!activeWorkspaceId) {
    toast('请先新增工作区', 'error');
    return;
  }
  try {
    const data = await backendApi.files.openFolder(activeWorkspaceId, workspaceCurrentDir || '');
    if (!data.ok) {
      toast(data.error || '打开失败', 'error');
      return;
    }
    toast('已打开当前文件夹', 'success');
  } catch(e) {
    toast('打开失败: ' + e.message, 'error');
  }
}

function renderWorkspaceFileList(files, dirPath, parentDir) {
  const el = document.getElementById('workspaceFileList');
  let html = `<div class="codex-history-empty" style="padding:8px 12px;text-align:left;">${escapeHtml(activeWorkspaceLabel())}: ${escapeHtml(dirPath || '/')}</div>`;
  if (parentDir !== null) {
    html += `<div class="file-item dir-back" data-action="dir" data-path="${escapeHtml(parentDir)}">
      <span class="file-icon">⬆</span><span class="file-name">..</span></div>`;
  }
  for (const f of files) {
    const icon = f.is_dir ? '📁' : fileIcon(f.name);
    const active = workspaceCurrentFile === f.path ? ' active' : '';
    const action = f.is_dir ? 'dir' : 'file';
    const size = f.is_dir ? '' : formatSize(f.size);
    const download = f.is_dir ? '' : `<button class="file-action" data-download="${escapeHtml(f.path)}">下载</button>`;
    html += `<div class="file-item${active}" data-action="${action}" data-path="${escapeHtml(f.path)}">
      <span class="file-icon">${icon}</span>
      <span class="file-name">${escapeHtml(f.name)}</span>
      <span class="file-size">${size}</span>
      ${download}
    </div>`;
  }
  if (!files.length && parentDir === null) {
    html += '<div class="codex-history-empty">目录为空</div>';
  }
  el.innerHTML = html;
}

async function openWorkspaceFile(path) {
  if (!activeWorkspaceId) return;
  if (workspaceModified && !confirm('当前工作区文件未保存，是否继续？')) return;
  const workspaceId = activeWorkspaceId;
  const requestSeq = ++workspaceFileReadRequestSeq;
  try {
    const data = await backendApi.files.read(workspaceId, path);
    if (requestSeq !== workspaceFileReadRequestSeq || workspaceId !== activeWorkspaceId) return;
    workspaceCurrentFile = path;
    workspaceOriginalContent = data.content;
    workspaceModified = false;
    document.getElementById('workspaceEditor').value = data.content;
    document.getElementById('workspaceFilePath').textContent = activeWorkspaceLabel() + ': ' + path;
    document.getElementById('workspaceStatusSize').textContent = formatSize(data.size);
    document.getElementById('workspaceStatusModified').textContent = data.modified;
    updateWorkspaceChars();
    showWorkspaceEditorArea();
    loadWorkspaceFiles(workspaceCurrentDir);
  } catch(e) {
    toast('打开失败: ' + e.message, 'error');
  }
}

async function saveWorkspaceFile() {
  if (!activeWorkspaceId) return;
  if (!workspaceCurrentFile) return;
  const content = document.getElementById('workspaceEditor').value;
  try {
    const data = await backendApi.files.write(
      activeWorkspaceId,
      workspaceCurrentFile,
      content,
    );
    if (data.ok) {
      workspaceOriginalContent = content;
      workspaceModified = false;
      document.getElementById('workspaceStatusSize').textContent = formatSize(data.size);
      document.getElementById('workspaceStatusModified').textContent = data.modified;
      toast('保存成功', 'success');
      loadWorkspaceFiles(workspaceCurrentDir);
    } else {
      toast(data.error || '保存失败', 'error');
    }
  } catch(e) {
    toast('保存失败', 'error');
  }
}

function reloadWorkspaceFile() {
  if (workspaceCurrentFile) openWorkspaceFile(workspaceCurrentFile);
}

async function downloadWorkspaceFile(path = workspaceCurrentFile) {
  if (!path) return;
  const filename = path.split('/').pop();
  try {
    await backendApi.files.download(activeWorkspaceId, path, filename);
  } catch(e) {
    toast('下载失败: ' + e.message, 'error');
  }
}

function updateWorkspaceChars() {
  const v = document.getElementById('workspaceEditor').value;
  document.getElementById('workspaceStatusChars').textContent = v.length + ' 字符, ' + v.split('\n').length + ' 行';
}

function showWorkspaceEditorArea() {
  if (terminalFullscreen) {
    setTerminalFullscreen(false);
  }
  document.getElementById('terminalWorkArea').style.display = 'none';
  document.getElementById('workspaceEditorArea').classList.add('active');
}

function showTerminalArea() {
  document.getElementById('workspaceEditorArea').classList.remove('active');
  document.getElementById('terminalWorkArea').style.display = 'flex';
  if (typeof activateTerminalView === 'function') activateTerminalView();
  renderTerminalTabs();
  if (typeof scheduleTerminalLayoutSync === 'function') {
    scheduleTerminalLayoutSync({ claimGeometry: true, forceEmit: true, focus: true });
  } else {
    setTimeout(() => {
      if (termFit) {
        try { termFit.fit(); } catch(e) {}
      }
      if (term) term.focus();
    }, 50);
  }
  scrollTerminalToBottom(40, activeTerminalView(), { force: false });
}

function resizeTerminalToContainer(delay = 80) {
  if (typeof scheduleTerminalLayoutSync === 'function') {
    setTimeout(() => {
      scheduleTerminalLayoutSync({ claimGeometry: true, forceEmit: true });
    }, Math.max(0, delay));
    return;
  }
  setTimeout(() => {
    if (termFit) {
      try { termFit.fit(); } catch(e) {}
    }
    if (termSocket && termConnected && term) {
      termSocket.emit(TERMINAL_SOCKET_EVENTS.resize, { cols: term.cols, rows: term.rows });
    }
    if (term) term.focus();
  }, delay);
}
