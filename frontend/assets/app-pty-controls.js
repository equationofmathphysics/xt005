// Optional CLI and history actions sit above the generic PTY transport.
async function ptyPost(path, payload) {
  const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || response.statusText);
  return result;
}
async function startOptionalAgent() {
  if (!activeWorkspaceId) return;
  const workspaceId = activeWorkspaceId;
  try {
    const result = await ptyPost('/api/workspace-terminals/agent', {workspace_id: workspaceId});
    upsertTerminalSummary(result.terminal);
    if (workspaceId === activeWorkspaceId) switchTerminal(result.terminal.id);
  } catch (error) { toast(error.message, 'error'); }
}
async function interruptActiveTerminal() {
  try {
    await ptyPost('/api/workspace-terminals/interrupt', {workspace_id: activeWorkspaceId, terminal_id: activeTerminalId});
    toast('中断键已发送，请在终端确认执行结果');
  } catch (error) { toast(error.message, 'error'); }
}
async function toggleWorkspacePin(id) {
  try {
    const workspace = workspaceList.find(item => item.id === id);
    const result = await ptyPost('/api/workspaces/pin', {workspace_id: id, pinned: !workspace?.pinned});
    // Preserve terminal summaries already loaded into the page.
    workspaceList = result.workspaces.map(item => ({...workspaceList.find(old => old.id === item.id), ...item}));
    renderWorkspaceSwitch();
  } catch (error) { toast(error.message, 'error'); }
}
function makePtyDialog(title) {
  const dialog = document.createElement('dialog');
  dialog.className = 'pty-dialog';
  const header = document.createElement('header');
  const heading = document.createElement('strong'); heading.textContent = title;
  const close = document.createElement('button'); close.textContent = '关闭'; close.onclick = () => dialog.close();
  header.append(heading, close); dialog.append(header);
  dialog.addEventListener('close', () => dialog.remove()); document.body.append(dialog); dialog.showModal();
  return dialog;
}
async function previewConversation(threadId) {
  const workspaceId = activeWorkspaceId;
  const dialog = makePtyDialog('本地历史 · ' + (codexHistoryById.get(threadId)?.title || threadId));
  const body = document.createElement('div'); dialog.append(body);
  const more = document.createElement('button'); more.textContent = '加载更多'; dialog.append(more);
  let cursor = 0;
  async function load() {
    more.disabled = true;
    try {
      const query = new URLSearchParams({workspace_id: workspaceId, cursor: String(cursor)});
      const response = await fetch(`/api/codex-history/${encodeURIComponent(threadId)}/read?${query}`);
      const page = await response.json();
      if (!response.ok) throw new Error(page.error || response.statusText);
      for (const record of page.records) {
        const item = document.createElement('details');
        item.open = ['user', 'assistant', 'user_message', 'agent_message', 'notice'].includes(record.type);
        const label = document.createElement('summary'); label.textContent = record.type;
        const text = document.createElement('pre'); text.textContent = record.text;
        item.append(label, text); body.append(item);
      }
      cursor = page.nextCursor; more.hidden = cursor === null;
    } catch (error) { toast(error.message, 'error'); }
    finally { more.disabled = false; }
  }
  more.onclick = load; await load();
}
const ptyDraftStore = new CodexwsDraftStore(() => localStorage);
function openTerminalDraft() {
  if (!activeWorkspaceId) return;
  const workspaceId = activeWorkspaceId;
  const terminalId = activeTerminalId;
  const threadId = activeTerminalInfo()?.thread_id || `terminal:${terminalId}`;
  const context = {workspaceId, threadId};
  const dialog = makePtyDialog('草稿 · ' + terminalLabel());
  const input = document.createElement('textarea'); input.value = ptyDraftStore.read(context).text;
  input.setAttribute('aria-label', '终端草稿'); dialog.append(input);
  const status = document.createElement('p'); dialog.append(status);
  function save() {
    ptyDraftStore.saveText(context, input.value);
    status.textContent = ptyDraftStore.unsaved.has(ptyDraftStore.key(context)) ? '浏览器存储不可用，草稿只保留在本页' : '草稿已保存在此浏览器';
  }
  input.oninput = save;
  const recover = document.createElement('button'); recover.textContent = '恢复旧版未确认发送';
  recover.hidden = !ptyDraftStore.read(context).pending.length;
  recover.onclick = () => { input.value = ptyDraftStore.recover(context); recover.hidden = true; save(); };
  const paste = document.createElement('button'); paste.textContent = '粘贴到终端';
  paste.onclick = () => {
    if (activeWorkspaceId !== workspaceId || activeTerminalId !== terminalId || !termConnected || !termSocket?.connected) {
      status.textContent = '请先连接原终端，再粘贴'; return;
    }
    term.paste(input.value); // Keep the draft until the user confirms the terminal received it.
    status.textContent = '已请求粘贴；草稿仍保留，请在终端检查后提交';
  };
  dialog.append(recover, paste); save(); input.focus();
}
