let fullscreenTransitionPending = false;
let fullscreenLayoutSettleUntil = 0;
const FULLSCREEN_LAYOUT_SETTLE_MS = 240;
const BIG_SCREEN_GRID_MAX_FONT_SIZE = 11;
const BIG_SCREEN_FOCUSED_MAX_FONT_SIZE = 14;
const BIG_SCREEN_MIN_FONT_SIZE = 5;
const BIG_SCREEN_PENDING_INPUT_LIMIT = 65536;

function markFullscreenLayoutTransition() {
  fullscreenLayoutSettleUntil = Date.now() + FULLSCREEN_LAYOUT_SETTLE_MS;
}

function fullscreenLayoutSettleDelay() {
  return Math.max(0, fullscreenLayoutSettleUntil - Date.now());
}

function setFullscreenTransitionPending(pending) {
  fullscreenTransitionPending = Boolean(pending);
  markFullscreenLayoutTransition();
  for (const id of ['pageFullscreenBtn', 'terminalFullscreenBtn']) {
    const button = document.getElementById(id);
    if (button) button.disabled = fullscreenTransitionPending;
  }
}

function updateTerminalFullscreenUi(enabled) {
  terminalFullscreen = Boolean(enabled);
  const bigScreen = document.getElementById('terminalBigScreen');
  const terminalWorkArea = document.getElementById('terminalWorkArea');
  const fullscreenBtn = document.getElementById('terminalFullscreenBtn');
  if (terminalWorkArea) terminalWorkArea.classList.remove('fullscreen');
  if (bigScreen) {
    bigScreen.classList.toggle('active', terminalFullscreen);
    bigScreen.setAttribute('aria-hidden', terminalFullscreen ? 'false' : 'true');
  }
  if (fullscreenBtn) {
    fullscreenBtn.textContent = terminalFullscreen ? '退出大屏' : '大屏';
    fullscreenBtn.setAttribute('aria-pressed', terminalFullscreen ? 'true' : 'false');
  }
}

function updatePageFullscreenUi(enabled) {
  pageFullscreen = Boolean(enabled);
  const btn = document.getElementById('pageFullscreenBtn');
  if (!btn) return;
  btn.textContent = pageFullscreen ? '退出全屏' : '页面全屏';
  btn.setAttribute('aria-pressed', pageFullscreen ? 'true' : 'false');
}

function browserFullscreenElement() {
  return document.fullscreenElement || document.webkitFullscreenElement || null;
}

function requestBrowserFullscreen(element) {
  if (element.requestFullscreen) {
    return element.requestFullscreen({ navigationUI: 'hide' });
  }
  if (element.webkitRequestFullscreen) {
    element.webkitRequestFullscreen();
    return Promise.resolve();
  }
  return Promise.reject(new Error('浏览器不支持全屏 API'));
}

function exitBrowserFullscreen() {
  if (document.exitFullscreen) {
    return document.exitFullscreen();
  }
  if (document.webkitExitFullscreen) {
    document.webkitExitFullscreen();
    return Promise.resolve();
  }
  return Promise.resolve();
}

function terminalFullscreenElement() {
  return document.getElementById('terminalBigScreen') || document.getElementById('terminalWorkArea');
}

function pageFullscreenElement() {
  return document.documentElement;
}

function syncMainTerminalAfterFullscreen() {
  if (typeof scheduleTerminalLayoutSync === 'function') {
    scheduleTerminalLayoutSync({ claimGeometry: true, forceEmit: true });
    return;
  }
  resizeTerminalToContainer(0);
}

function workspaceBorderColor(workspaceId) {
  const value = String(workspaceId || '');
  let hash = 0;
  for (let i = 0; i < value.length; i++) {
    hash = ((hash << 5) - hash + value.charCodeAt(i)) | 0;
  }
  return WORKSPACE_BORDER_COLORS[Math.abs(hash) % WORKSPACE_BORDER_COLORS.length];
}

function bigScreenTerminalKey(terminal) {
  return (terminal.workspace_id || '') + ':' + (terminal.id || DEFAULT_TERMINAL);
}

function terminalObservationFromSummary(terminal) {
  return terminal.observation || terminalObservation(terminal.workspace_id, terminal.id || DEFAULT_TERMINAL);
}

function terminalLastOutputAt(terminal) {
  const observation = terminalObservationFromSummary(terminal);
  return Number(terminal.last_output_at || observation.last_output_at || 0);
}

function terminalHasRunningHook(terminal, nowSec) {
  const event = latestCodexTurnEvent(terminal, terminal.workspace_id);
  if ((event?.status || '').toLowerCase() !== 'running') return false;
  const ts = Number(event.timestamp || 0);
  return !ts || nowSec - ts <= 600;
}

function terminalHasActiveCodex(terminal) {
  return Boolean(terminal.codex_active || terminal.has_codex_process);
}

function terminalActivityScore(terminal, nowSec) {
  if (!terminal.alive || !terminal.usable) return -1;
  const lastOutputAt = terminalLastOutputAt(terminal);
  const outputAge = lastOutputAt ? nowSec - lastOutputAt : Number.POSITIVE_INFINITY;
  let score = 10;
  if (terminalHasActiveCodex(terminal)) score += 1000;
  if (terminalHasRunningHook(terminal, nowSec)) score += 800;
  if (outputAge <= 30) score += 300;
  else if (outputAge <= BIG_SCREEN_RECENT_OUTPUT_SECONDS) score += 150;
  else if (outputAge <= 600) score += 35;
  if (Number.isFinite(outputAge)) score += Math.max(0, 30 - Math.min(outputAge, 30));
  return score;
}

function terminalActiveForBigScreen(terminal, nowSec) {
  if (!terminal.alive || !terminal.usable) return false;
  if (terminalHasActiveCodex(terminal)) return true;
  if (terminalHasRunningHook(terminal, nowSec)) return true;
  const lastOutputAt = terminalLastOutputAt(terminal);
  return Boolean(lastOutputAt && nowSec - lastOutputAt <= BIG_SCREEN_RECENT_OUTPUT_SECONDS);
}

function bigScreenActivityMeta(terminal, nowSec) {
  if (terminalHasActiveCodex(terminal)) return { label: 'CODEX', className: 'codex' };
  if (terminalHasRunningHook(terminal, nowSec)) return { label: 'RUN', className: 'codex' };
  const lastOutputAt = terminalLastOutputAt(terminal);
  if (lastOutputAt && nowSec - lastOutputAt <= BIG_SCREEN_RECENT_OUTPUT_SECONDS) {
    return { label: 'OUTPUT', className: 'output' };
  }
  return { label: 'IDLE', className: 'idle' };
}

function allTerminalsFromStats(data = {}) {
  const terminals = [];
  const seen = new Set();
  const addTerminal = (terminal, workspaceId = '') => {
    if (!terminal) return;
    const resolvedWorkspaceId = terminal.workspace_id || workspaceId;
    if (!resolvedWorkspaceId) return;
    const id = terminal.id || terminal.terminal_id || DEFAULT_TERMINAL;
    const key = resolvedWorkspaceId + ':' + id;
    if (seen.has(key)) return;
    seen.add(key);
    terminals.push({
      ...terminal,
      id,
      name: terminal.name || id,
      workspace_id: resolvedWorkspaceId,
      observation: terminal.observation || terminalObservation(resolvedWorkspaceId, id),
    });
  };

  if (data.terminals_by_workspace) {
    for (const [workspaceId, items] of Object.entries(data.terminals_by_workspace)) {
      if (!Array.isArray(items)) continue;
      items.forEach(item => addTerminal(item, workspaceId));
    }
  }

  if (data.terminals) {
    for (const [key, item] of Object.entries(data.terminals)) {
      const parts = String(key).split(':');
      addTerminal({ ...item, id: item.id || parts[1] || DEFAULT_TERMINAL }, parts[0] || '');
    }
  }

  return terminals;
}

function activeBigScreenTerminalsFromStats(data = {}) {
  const nowSec = Number(data.timestamp || Math.floor(Date.now() / 1000));
  return allTerminalsFromStats(data)
    .filter(terminal => terminalActiveForBigScreen(terminal, nowSec))
    .map(terminal => ({
      ...terminal,
      _activity_score: terminalActivityScore(terminal, nowSec),
      _activity: bigScreenActivityMeta(terminal, nowSec),
    }))
    .sort((a, b) => {
      const score = b._activity_score - a._activity_score;
      if (score) return score;
      const workspace = workspaceLabelById(a.workspace_id).localeCompare(workspaceLabelById(b.workspace_id));
      if (workspace) return workspace;
      return String(a.name || a.id).localeCompare(String(b.name || b.id));
    });
}

function usableBigScreenTerminalsFromStats(data = {}) {
  const nowSec = Number(data.timestamp || Math.floor(Date.now() / 1000));
  return allTerminalsFromStats(data)
    .filter(terminal => terminal.alive && terminal.usable)
    .map(terminal => ({
      ...terminal,
      _activity_score: terminalActivityScore(terminal, nowSec),
      _activity: bigScreenActivityMeta(terminal, nowSec),
    }))
    .sort((a, b) => {
      const score = b._activity_score - a._activity_score;
      if (score) return score;
      const workspace = workspaceLabelById(a.workspace_id).localeCompare(workspaceLabelById(b.workspace_id));
      if (workspace) return workspace;
      return String(a.name || a.id).localeCompare(String(b.name || b.id));
    });
}

function stableBigScreenTerminalSelection(candidates) {
  const byKey = new Map(candidates.map(item => [bigScreenTerminalKey(item), item]));
  const selected = [];
  const selectedKeys = new Set();
  for (const key of bigScreenTiles.keys()) {
    const item = byKey.get(key);
    if (!item) continue;
    selected.push(item);
    selectedKeys.add(key);
    if (selected.length >= BIG_SCREEN_LIMIT) return selected;
  }
  for (const item of candidates) {
    const key = bigScreenTerminalKey(item);
    if (selectedKeys.has(key)) continue;
    selected.push(item);
    selectedKeys.add(key);
    if (selected.length >= BIG_SCREEN_LIMIT) break;
  }
  return selected;
}

function bigScreenTerminalViewFromStats(data = {}) {
  const active = activeBigScreenTerminalsFromStats(data);
  if (active.length) {
    return {
      terminals: stableBigScreenTerminalSelection(active),
      activeCount: active.length,
      fallback: false,
      totalUsable: allTerminalsFromStats(data).filter(item => item.alive && item.usable).length,
    };
  }
  const usable = usableBigScreenTerminalsFromStats(data);
  return {
    terminals: stableBigScreenTerminalSelection(usable),
    activeCount: 0,
    fallback: usable.length > 0,
    totalUsable: usable.length,
  };
}

function resetBigScreenTerminal(tile) {
  if (!tile?.term) return;
  try { tile.term.reset(); } catch(e) {}
  try { tile.term.clear(); } catch(e) {}
  try { tile.term.write('\x1b[2J\x1b[3J\x1b[H'); } catch(e) {}
}

function preserveBigScreenSurface(tile) {
  if (!tile?.terminalEl || tile.syncState === 'disposed') return false;
  if (tile.surfaceOverlay?.isConnected) return true;
  const overlay = captureTerminalSurface(
    tile.terminalEl,
    BIG_SCREEN_TERMINAL_THEME.background || '#0b1018',
  );
  if (!overlay) return false;
  tile.terminalEl.appendChild(overlay);
  tile.surfaceOverlay = overlay;
  return true;
}

function releaseBigScreenSurface(tile) {
  const overlay = tile?.surfaceOverlay;
  if (!overlay) return;
  tile.surfaceOverlay = null;
  try { overlay.remove(); } catch(e) {}
}

function writeBigScreenReplayBuffer(tile, buffer) {
  if (!tile?.term) return;
  const value = String(buffer || '').slice(-BIG_SCREEN_REPLAY_CHARS);
  if (!value) return;
  try {
    tile.term.write(value);
  } catch(e) {}
}

function createBigScreenTile(summary) {
  const tileEl = document.createElement('div');
  tileEl.className = 'big-screen-tile';

  const header = document.createElement('div');
  header.className = 'big-screen-tile-header';
  const dot = document.createElement('span');
  dot.className = 'big-screen-ws-dot';
  const label = document.createElement('span');
  label.className = 'big-screen-label';
  const status = document.createElement('span');
  status.className = 'big-screen-status idle';
  header.append(dot, label, status);

  const terminalEl = document.createElement('div');
  terminalEl.className = 'big-screen-terminal';
  tileEl.append(header, terminalEl);

  const tile = {
    key: bigScreenTerminalKey(summary),
    el: tileEl,
    terminalEl,
    term: null,
    fit: null,
    resizeObserver: null,
    socket: null,
    requestAttach: null,
    summary,
    labelEl: label,
    statusEl: status,
    replaySeq: 0,
    replaying: false,
    pendingOutput: '',
    frameMode: false,
    frameStreamId: '',
    frameStreamEpoch: 0,
    frameAppliedSeq: -1,
    frameAppliedEnd: 0,
    frameGeneration: 0,
    frameWriting: null,
    pendingFrame: null,
    frameFetchInFlight: null,
    frameFetchAbort: null,
    frameFetchDirty: false,
    frameRecoveryTimer: 0,
    frameRecoveryAttempts: 0,
    syncState: 'detached',
    attachSeq: 0,
    deltaQueue: [],
    deltaBacklog: [],
    deltaWriting: null,
    deltaAcceptedSeq: -1,
    deltaAcceptedEnd: 0,
    deltaSignatures: new Map(),
    altScanTail: '',
    surfaceOverlay: null,
    interactive: false,
    inputReady: false,
    pendingInput: '',
    inputDisposable: null,
    attachViewOnly: true,
    visualMode: '',
  };

  tileEl.tabIndex = 0;
  tileEl.setAttribute('role', 'button');
  tileEl.addEventListener('click', () => setBigScreenFocus(tile.key));
  tileEl.addEventListener('keydown', event => {
    if (event.target !== tileEl) return;
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    setBigScreenFocus(tile.key);
  });
  return tile;
}

function fitBigScreenTileVisual(tile) {
  if (!tile?.term || !tile.terminalEl) return;
  const rect = tile.terminalEl.getBoundingClientRect();
  if (!rect || rect.width < 40 || rect.height < 30) return;
  const visualMode = tile.el?.classList.contains('focused') ? 'focused' : 'grid';
  const maximumFontSize = visualMode === 'focused'
    ? BIG_SCREEN_FOCUSED_MAX_FONT_SIZE
    : BIG_SCREEN_GRID_MAX_FONT_SIZE;
  if (tile.visualMode !== visualMode) {
    tile.visualMode = visualMode;
    try { tile.term.options.fontSize = maximumFontSize; } catch(e) {}
  }
  const dimensions = tile.term._core?._renderService?.dimensions?.css?.cell;
  if (!dimensions?.width || !dimensions?.height) return;
  const cols = Math.max(2, Number(tile.term.cols || 80));
  const rows = Math.max(1, Number(tile.term.rows || 24));
  const currentFontSize = Math.max(
    BIG_SCREEN_MIN_FONT_SIZE,
    Number(tile.term.options.fontSize || maximumFontSize),
  );
  const scale = Math.min(
    (rect.width - 4) / (cols * dimensions.width),
    (rect.height - 4) / (rows * dimensions.height)
  );
  if (!Number.isFinite(scale) || scale <= 0) return;
  const nextFontSize = Math.max(
    BIG_SCREEN_MIN_FONT_SIZE,
    Math.min(maximumFontSize, Math.floor(currentFontSize * scale * 10) / 10),
  );
  if (Math.abs(nextFontSize - currentFontSize) >= 0.2) {
    try { tile.term.options.fontSize = nextFontSize; } catch(e) {}
  }
  try { tile.term.refresh(0, Math.max(0, tile.term.rows - 1)); } catch(e) {}
}

function ensureBigScreenTileTerminal(tile) {
  if (!tile || tile.term) return;
  const miniTerm = new Terminal({
    cursorBlink: Boolean(tile.interactive),
    disableStdin: !tile.interactive,
    fontSize: BIG_SCREEN_GRID_MAX_FONT_SIZE,
    scrollback: BIG_SCREEN_SCROLLBACK_LINES,
    convertEol: false,
    windowsMode: false,
    allowProposedApi: false,
    fontFamily: '"JetBrains Mono", "Fira Code", "Consolas", monospace',
    theme: BIG_SCREEN_TERMINAL_THEME,
  });
  miniTerm.open(tile.terminalEl);
  tile.term = miniTerm;
  tile.inputDisposable = miniTerm.onData(data => emitBigScreenTerminalInput(tile, data));
  if (typeof installTerminalImeFallback === 'function') {
    installTerminalImeFallback(tile.terminalEl, data => emitBigScreenTerminalInput(tile, data));
  }
  tile.resizeObserver = new ResizeObserver(() => {
    fitBigScreenTileVisual(tile);
  });
  tile.resizeObserver.observe(tile.terminalEl);
  setTimeout(() => {
    fitBigScreenTileVisual(tile);
  }, 0);
}

function queueBigScreenTerminalInput(tile, data) {
  if (!tile) return false;
  tile.pendingInput = (tile.pendingInput + String(data || '')).slice(-BIG_SCREEN_PENDING_INPUT_LIMIT);
  return true;
}

function flushBigScreenTerminalInput(tile) {
  if (
    !tile?.interactive
    || !tile.inputReady
    || tile.replaying
    || !tile.pendingInput
    || !tile.socket?.connected
  ) return false;
  const data = tile.pendingInput;
  tile.pendingInput = '';
  tile.socket.emit(TERMINAL_SOCKET_EVENTS.input, {
    attach_seq: tile.attachSeq,
    data,
  });
  return true;
}

function emitBigScreenTerminalInput(tile, data) {
  if (!tile?.interactive || tile.key !== bigScreenFocusedKey) return false;
  if (tile.replaying && isGeneratedTerminalResponsePacket(data)) return false;
  const value = normalizeTerminalInput(data);
  if (!value) return false;
  if (tile.replaying || !tile.inputReady || !tile.socket?.connected) {
    return queueBigScreenTerminalInput(tile, value);
  }
  tile.socket.emit(TERMINAL_SOCKET_EVENTS.input, {
    attach_seq: tile.attachSeq,
    data: value,
  });
  return true;
}

function updateBigScreenTileInteraction(tile, interactive) {
  if (!tile) return;
  const nextInteractive = Boolean(interactive);
  const changed = tile.interactive !== nextInteractive;
  tile.interactive = nextInteractive;
  if (!nextInteractive) {
    tile.inputReady = false;
    tile.pendingInput = '';
  }
  if (tile.term) {
    try { tile.term.options.disableStdin = !nextInteractive; } catch(e) {}
    try { tile.term.options.cursorBlink = nextInteractive; } catch(e) {}
  }
  if (changed) tile.requestAttach?.();
}

function updateBigScreenTile(tile, summary) {
  tile.summary = summary;
  const color = workspaceBorderColor(summary.workspace_id);
  if (tile.el.style.getPropertyValue('--ws-border-color') !== color) {
    tile.el.style.setProperty('--ws-border-color', color);
  }
  const label = workspaceLabelById(summary.workspace_id) + ' / ' + (summary.name || summary.id || DEFAULT_TERMINAL);
  if (tile.labelEl.textContent !== label) tile.labelEl.textContent = label;
  if (tile.el.title !== label) tile.el.title = label;
  if (tile.el.getAttribute('aria-label') !== '聚焦终端 ' + label) {
    tile.el.setAttribute('aria-label', '聚焦终端 ' + label);
  }
  const activity = summary._activity || { label: 'IDLE', className: 'idle' };
  const statusClass = 'big-screen-status ' + activity.className;
  if (tile.statusEl.textContent !== activity.label) tile.statusEl.textContent = activity.label;
  if (tile.statusEl.className !== statusClass) tile.statusEl.className = statusClass;
}

function setBigScreenTileConnectionStatus(tile, text) {
  if (!tile?.statusEl) return;
  tile.statusEl.textContent = String(text || 'IDLE');
  tile.statusEl.className = 'big-screen-status idle';
}

function normalizeBigScreenFrame(data = {}) {
  const snapshot = String(data.screen_snapshot || '');
  const streamId = String(data.stream_id || '');
  const streamEpoch = Math.max(0, Math.floor(Number(data.stream_epoch || 0)));
  const frameSeq = Math.floor(Number(data.frame_seq));
  const frameEnd = Math.floor(Number(data.frame_end ?? data.screen_snapshot_end ?? data.history_end));
  if (!snapshot || !streamId || !Number.isFinite(frameSeq) || !Number.isFinite(frameEnd)) return null;
  return {
    snapshot,
    streamId,
    streamEpoch,
    frameSeq,
    frameEnd,
    deltaSafe: data.screen_snapshot_delta_safe === true,
    historyStart: Math.floor(Number(data.history_start ?? 0)),
    historyEnd: Math.floor(Number(data.history_end ?? frameEnd)),
    cols: Math.max(2, Math.floor(Number(data.screen_snapshot_cols || 80))),
    rows: Math.max(1, Math.floor(Number(data.screen_snapshot_rows || 24))),
  };
}

function normalizeBigScreenDelta(data = {}) {
  const streamId = String(data.stream_id || '');
  const streamEpoch = Math.max(0, Math.floor(Number(data.stream_epoch || 0)));
  const frameSeq = Math.floor(Number(data.frame_seq));
  const outputStart = Math.floor(Number(data.output_start));
  const outputEnd = Math.floor(Number(data.output_end));
  const eventType = data.event_type === 'resize' ? 'resize' : 'output';
  const rawCols = Number(data.cols);
  const rawRows = Number(data.rows);
  if (
    !streamId
    || !Number.isFinite(frameSeq)
    || !Number.isFinite(outputStart)
    || !Number.isFinite(outputEnd)
    || outputEnd < outputStart
  ) return null;
  const delta = {
    streamId,
    streamEpoch,
    frameSeq,
    outputStart,
    outputEnd,
    eventType,
    data: String(data.data || ''),
  };
  if (eventType === 'resize') {
    if (
      !Number.isFinite(rawCols)
      || rawCols <= 0
      || !Number.isFinite(rawRows)
      || rawRows <= 0
      || outputStart !== outputEnd
    ) return null;
    delta.cols = Math.max(2, Math.floor(rawCols));
    delta.rows = Math.max(1, Math.floor(rawRows));
  } else {
    const sanitized = delta.data
      .replace(/\x1b\[[0-9;?=>]*[cnR]/g, '')
      .replace(/\x1b\](?:10|11);[^\x07\x1b]*(?:\x07|\x1b\\)/g, '');
    if ([...sanitized].length !== outputEnd - outputStart) return null;
  }
  return delta;
}

function inspectBigScreenAlternateBuffer(tile, delta) {
  const value = String(tile?.altScanTail || '') + String(delta?.data || '');
  const modePattern = /\x1b\[\?([0-9;]*)([hl])/g;
  let match;
  while ((match = modePattern.exec(value))) {
    const modes = match[1].split(';').map(Number);
    if (modes.some(mode => mode === 47 || mode === 1047 || mode === 1049)) {
      return { changesBuffer: true, tail: '' };
    }
  }
  const tailMatch = value.match(/\x1b(?:\[(?:\?[0-9;]*)?)?$/);
  return {
    changesBuffer: false,
    tail: tailMatch ? tailMatch[0].slice(-64) : '',
  };
}

function sameBigScreenDelta(left, right) {
  return Boolean(
    left
    && right
    && left.streamId === right.streamId
    && left.streamEpoch === right.streamEpoch
    && left.frameSeq === right.frameSeq
    && left.outputStart === right.outputStart
    && left.outputEnd === right.outputEnd
    && left.eventType === right.eventType
    && left.data === right.data
    && Number(left.cols || 0) === Number(right.cols || 0)
    && Number(left.rows || 0) === Number(right.rows || 0)
  );
}

function sameBigScreenFrame(left, right) {
  return Boolean(
    left
    && right
    && left.streamId === right.streamId
    && left.streamEpoch === right.streamEpoch
    && left.frameSeq === right.frameSeq
    && left.frameEnd === right.frameEnd
    && left.historyStart === right.historyStart
    && left.historyEnd === right.historyEnd
    && left.cols === right.cols
    && left.rows === right.rows
    && left.deltaSafe === right.deltaSafe
    && left.snapshot === right.snapshot
  );
}

function rememberBigScreenSignature(tile, delta) {
  if (!tile || !delta) return;
  const key = delta.streamId + ':' + delta.frameSeq;
  tile.deltaSignatures.set(key, delta);
  while (tile.deltaSignatures.size > 4096) {
    tile.deltaSignatures.delete(tile.deltaSignatures.keys().next().value);
  }
}

function beginBigScreenAttach(tile) {
  if (!tile) return 0;
  preserveBigScreenSurface(tile);
  tile.frameGeneration += 1;
  tile.attachSeq += 1;
  if (tile.frameRecoveryTimer) clearTimeout(tile.frameRecoveryTimer);
  tile.frameRecoveryTimer = 0;
  try { tile.frameFetchAbort?.abort(); } catch(e) {}
  tile.frameFetchAbort = null;
  tile.frameFetchInFlight = null;
  tile.frameFetchDirty = false;
  tile.frameRecoveryAttempts = 0;
  tile.syncState = 'awaiting-frame';
  tile.frameMode = false;
  tile.replaying = true;
  tile.pendingFrame = null;
  tile.deltaQueue = [];
  tile.deltaBacklog = [];
  tile.deltaAcceptedSeq = -1;
  tile.deltaAcceptedEnd = 0;
  tile.deltaSignatures.clear();
  tile.frameAppliedSeq = -1;
  tile.frameAppliedEnd = 0;
  tile.frameStreamId = '';
  tile.altScanTail = '';
  return tile.attachSeq;
}

function rememberBigScreenDelta(tile, delta) {
  if (!tile || !delta) return false;
  const signatureKey = delta.streamId + ':' + delta.frameSeq;
  const signed = tile.deltaSignatures.get(signatureKey);
  if (signed) {
    if (!sameBigScreenDelta(signed, delta)) requestBigScreenFrameRecovery(tile, 'conflicting-delta');
    return false;
  }
  const existing = [tile.deltaWriting, ...tile.deltaQueue, ...tile.deltaBacklog]
    .find(item => item && item.streamId === delta.streamId && item.frameSeq === delta.frameSeq);
  if (existing) {
    if (!sameBigScreenDelta(existing, delta)) requestBigScreenFrameRecovery(tile, 'conflicting-delta');
    return false;
  }
  tile.deltaBacklog.push(delta);
  rememberBigScreenSignature(tile, delta);
  return true;
}

function queuePendingBigScreenFrame(tile, data, options = {}) {
  const incoming = normalizeBigScreenFrame(data);
  if (!incoming) return false;
  const current = normalizeBigScreenFrame(tile.pendingFrame?.data || {});
  if (sameBigScreenFrame(current, incoming)) return true;
  const shouldReplace = !current
    || (incoming.streamId === current.streamId && incoming.frameSeq > current.frameSeq)
    || (
      incoming.streamId === current.streamId
      && incoming.streamEpoch === current.streamEpoch
      && incoming.frameSeq === current.frameSeq
      && options.authoritative
    )
    || (incoming.streamId !== current.streamId && options.authoritative === true);
  if (shouldReplace) tile.pendingFrame = { data, options };
  return true;
}

function requestBigScreenFrameRecovery(tile, reason = 'gap') {
  if (!tile || tile.syncState === 'disposed' || tile.syncState === 'legacy') return;
  if (tile.syncState === 'awaiting-frame') return;
  tile.syncState = 'recovering';
  tile.replaying = true;
  tile.frameFetchDirty = true;
  if (tile.frameFetchInFlight || tile.frameRecoveryTimer || tile.frameWriting || tile.pendingFrame) return;

  const delay = tile.frameRecoveryAttempts
    ? Math.min(4000, 200 * (2 ** Math.min(5, tile.frameRecoveryAttempts - 1)))
    : 0;
  tile.frameRecoveryTimer = setTimeout(() => {
    tile.frameRecoveryTimer = 0;
    recoverBigScreenFrame(tile, reason);
  }, delay);
}

async function recoverBigScreenFrame(tile, reason = 'gap') {
  if (!tile || tile.frameFetchInFlight || !tile.summary || tile.syncState === 'disposed') return false;
  const generation = tile.frameGeneration;
  const abortController = new AbortController();
  tile.frameFetchAbort = abortController;
  tile.frameFetchDirty = false;
  const params = {
    workspace_id: tile.summary.workspace_id,
    terminal_id: tile.summary.id || DEFAULT_TERMINAL,
    stream_id: tile.frameStreamId || '',
    after_seq: String(Math.max(-1, Number(tile.frameAppliedSeq ?? -1))),
    reason,
  };
  const request = backendApi.terminals.screen(params, {
    timeout: 8000,
    signal: abortController.signal,
  })
    .then(data => {
      if (tile.frameGeneration !== generation || tile.syncState === 'disposed') return false;
      return applyBigScreenFrame(tile, data, { authoritative: true, reason });
    })
    .catch(() => false);
  tile.frameFetchInFlight = request;
  let recovered = false;
  try {
    recovered = await request;
    return recovered;
  } finally {
    if (tile.frameFetchInFlight === request) tile.frameFetchInFlight = null;
    if (tile.frameFetchAbort === abortController) tile.frameFetchAbort = null;
    if (tile.frameGeneration === generation && tile.syncState === 'recovering') {
      if (!recovered) tile.frameRecoveryAttempts += 1;
      if (!recovered || tile.frameFetchDirty) requestBigScreenFrameRecovery(tile, reason);
    }
  }
}

function continueBigScreenSync(tile) {
  if (!tile || tile.syncState === 'disposed' || tile.frameWriting || tile.deltaWriting) return;
  const pending = tile.pendingFrame;
  if (pending) {
    tile.pendingFrame = null;
    applyBigScreenFrame(tile, pending.data, pending.options);
    return;
  }
  if (tile.syncState === 'recovering') {
    requestBigScreenFrameRecovery(tile, 'pending-recovery');
    return;
  }
  if (tile.syncState === 'live') {
    reconcileBigScreenDeltas(tile);
    pumpBigScreenDeltas(tile);
  }
}

function applyBigScreenFrame(tile, data, options = {}) {
  const frame = normalizeBigScreenFrame(data);
  if (!tile?.term || !frame || !frame.deltaSafe || tile.syncState === 'disposed') return false;
  if (tile.frameWriting || tile.deltaWriting) {
    queuePendingBigScreenFrame(tile, data, options);
    tile.syncState = 'recovering';
    tile.replaying = true;
    return true;
  }

  const streamChanged = Boolean(tile.frameStreamId && tile.frameStreamId !== frame.streamId);
  if (streamChanged) {
    if (!options.authoritative) return false;
    tile.frameGeneration += 1;
  } else if (tile.frameStreamId === frame.streamId) {
    const appliedSeq = Number(tile.frameAppliedSeq ?? -1);
    const healthyNoBacklog = Boolean(
      tile.syncState === 'live'
      && !tile.deltaWriting
      && !tile.deltaQueue.length
      && !tile.deltaBacklog.length
      && Number(tile.deltaAcceptedSeq ?? -1) === appliedSeq
    );
    if (frame.frameSeq < appliedSeq) return tile.syncState !== 'recovering';
    if (frame.frameSeq === appliedSeq && healthyNoBacklog) return true;
  }

  const newerQueuedStream = !streamChanged && tile.deltaBacklog.some(delta => (
    delta.streamId !== frame.streamId
  ));
  if (newerQueuedStream) {
    tile.syncState = 'recovering';
    requestBigScreenFrameRecovery(tile, 'newer-stream');
    return false;
  }

  tile.frameMode = true;
  tile.frameStreamId = frame.streamId;
  tile.frameStreamEpoch = streamChanged
    ? frame.streamEpoch
    : Math.max(Number(tile.frameStreamEpoch || 0), frame.streamEpoch);
  tile.syncState = 'applying-frame';
  tile.replaying = true;
  const generation = tile.frameGeneration;
  const frameJob = { frame, generation };
  tile.frameWriting = frameJob;
  const carried = tile.deltaQueue.concat(tile.deltaBacklog);
  tile.deltaQueue = [];
  tile.deltaBacklog = carried.filter(delta => (
    delta.streamId === frame.streamId && delta.frameSeq > frame.frameSeq
  ));
  tile.deltaAcceptedSeq = frame.frameSeq;
  tile.deltaAcceptedEnd = frame.frameEnd;
  for (const [key, delta] of tile.deltaSignatures) {
    if (delta.streamId !== frame.streamId || delta.frameSeq <= frame.frameSeq) {
      tile.deltaSignatures.delete(key);
    }
  }
  tile.altScanTail = '';
  preserveBigScreenSurface(tile);
  try { tile.term.reset(); } catch(e) {}
  try { tile.term.resize(frame.cols, frame.rows); } catch(e) {}
  fitBigScreenTileVisual(tile);

  const finish = success => {
    const current = Boolean(
      success
      && tile.frameWriting === frameJob
      && tile.frameGeneration === generation
      && tile.frameStreamId === frame.streamId
      && tile.syncState !== 'disposed'
    );
    if (tile.frameWriting === frameJob) tile.frameWriting = null;
    if (!current) {
      continueBigScreenSync(tile);
      return;
    }
    tile.frameAppliedSeq = frame.frameSeq;
    tile.frameAppliedEnd = frame.frameEnd;
    tile.frameRecoveryAttempts = 0;
    tile.frameFetchDirty = false;
    const pending = tile.pendingFrame;
    tile.pendingFrame = null;
    if (pending) {
      applyBigScreenFrame(tile, pending.data, pending.options);
      return;
    }
    tile.syncState = 'live';
    tile.replaying = false;
    flushBigScreenTerminalInput(tile);
    try { tile.term.scrollToBottom(); } catch(e) {}
    const reconciled = reconcileBigScreenDeltas(tile);
    if (reconciled) releaseBigScreenSurface(tile);
  };
  try {
    tile.term.write(frame.snapshot, () => finish(true));
  } catch(e) {
    finish(false);
    requestBigScreenFrameRecovery(tile, 'frame-write');
  }
  return true;
}

function acceptBigScreenDelta(tile, data) {
  if (tile?.syncState === 'legacy') {
    const text = String(data?.data || '');
    if (tile.replaying) {
      tile.pendingOutput += text;
    } else {
      try { tile.term?.write(text); } catch(e) {}
    }
    return true;
  }
  const delta = normalizeBigScreenDelta(data);
  if (!tile?.term || !delta || tile.syncState === 'disposed') {
    if (tile?.frameMode) requestBigScreenFrameRecovery(tile, 'invalid-delta');
    return false;
  }
  if (tile.syncState !== 'live' || tile.frameWriting || !tile.frameStreamId) {
    rememberBigScreenDelta(tile, delta);
    if (tile.syncState === 'recovering') tile.frameFetchDirty = true;
    return true;
  }
  if (delta.streamId !== tile.frameStreamId) {
    if (delta.streamEpoch && tile.frameStreamEpoch && delta.streamEpoch < tile.frameStreamEpoch) return false;
    rememberBigScreenDelta(tile, delta);
    requestBigScreenFrameRecovery(tile, 'stream-mismatch');
    return false;
  }
  const signatureKey = delta.streamId + ':' + delta.frameSeq;
  const signed = tile.deltaSignatures.get(signatureKey);
  if (signed) {
    if (!sameBigScreenDelta(signed, delta)) requestBigScreenFrameRecovery(tile, 'conflicting-delta');
    return sameBigScreenDelta(signed, delta);
  }
  if (delta.frameSeq <= Number(tile.deltaAcceptedSeq ?? -1)) return true;
  const alternate = inspectBigScreenAlternateBuffer(tile, delta);
  if (
    delta.frameSeq !== Number(tile.deltaAcceptedSeq ?? -1) + 1
    || delta.outputStart !== Number(tile.deltaAcceptedEnd || 0)
    || alternate.changesBuffer
  ) {
    rememberBigScreenDelta(tile, delta);
    requestBigScreenFrameRecovery(tile, alternate.changesBuffer ? 'alternate-buffer' : 'delta-gap');
    return false;
  }
  tile.altScanTail = alternate.tail;
  tile.deltaQueue.push(delta);
  rememberBigScreenSignature(tile, delta);
  tile.deltaAcceptedSeq = delta.frameSeq;
  tile.deltaAcceptedEnd = delta.outputEnd;
  pumpBigScreenDeltas(tile);
  return true;
}

function finishBigScreenDelta(tile, delta, generation, success) {
  if (tile.deltaWriting === delta) tile.deltaWriting = null;
  const sameRevision = Boolean(
    tile.frameGeneration === generation
    && tile.frameStreamId === delta.streamId
    && tile.syncState !== 'disposed'
  );
  const current = Boolean(
    success
    && sameRevision
  );
  if (!current) {
    if (!success && sameRevision) requestBigScreenFrameRecovery(tile, 'delta-write');
    continueBigScreenSync(tile);
    return;
  }
  tile.frameAppliedSeq = delta.frameSeq;
  tile.frameAppliedEnd = delta.outputEnd;
  const pending = tile.pendingFrame;
  if (pending) {
    tile.pendingFrame = null;
    applyBigScreenFrame(tile, pending.data, pending.options);
    return;
  }
  pumpBigScreenDeltas(tile);
}

function pumpBigScreenDeltas(tile) {
  if (
    !tile?.term
    || tile.deltaWriting
    || tile.frameWriting
    || tile.syncState !== 'live'
    || !tile.deltaQueue.length
  ) return;
  const delta = tile.deltaQueue.shift();
  const generation = tile.frameGeneration;
  tile.deltaWriting = delta;
  if (delta.eventType === 'resize') {
    let resized = true;
    try { tile.term.resize(delta.cols, delta.rows); } catch(e) { resized = false; }
    fitBigScreenTileVisual(tile);
    finishBigScreenDelta(tile, delta, generation, resized);
    return;
  }
  if (!delta.data) {
    finishBigScreenDelta(tile, delta, generation, true);
    return;
  }
  try {
    tile.term.write(delta.data, () => {
      try { tile.term.scrollToBottom(); } catch(e) {}
      finishBigScreenDelta(tile, delta, generation, true);
    });
  } catch(e) {
    finishBigScreenDelta(tile, delta, generation, false);
  }
}

function reconcileBigScreenDeltas(tile) {
  if (!tile || tile.syncState !== 'live' || tile.frameWriting) return false;
  const backlog = tile.deltaBacklog.slice().sort((left, right) => left.frameSeq - right.frameSeq);
  tile.deltaBacklog = [];
  let needsRecovery = false;
  for (const delta of backlog) {
    if (delta.streamId !== tile.frameStreamId) {
      if (!delta.streamEpoch || !tile.frameStreamEpoch || delta.streamEpoch >= tile.frameStreamEpoch) {
        tile.deltaBacklog.push(delta);
        needsRecovery = true;
      }
      continue;
    }
    if (delta.frameSeq <= Number(tile.deltaAcceptedSeq ?? -1)) continue;
    const alternate = inspectBigScreenAlternateBuffer(tile, delta);
    if (
      delta.frameSeq === Number(tile.deltaAcceptedSeq ?? -1) + 1
      && delta.outputStart === Number(tile.deltaAcceptedEnd || 0)
      && !alternate.changesBuffer
    ) {
      tile.deltaQueue.push(delta);
      tile.deltaSignatures.set(delta.streamId + ':' + delta.frameSeq, delta);
      tile.deltaAcceptedSeq = delta.frameSeq;
      tile.deltaAcceptedEnd = delta.outputEnd;
      tile.altScanTail = alternate.tail;
    } else {
      tile.deltaBacklog.push(delta);
      needsRecovery = true;
    }
  }
  pumpBigScreenDeltas(tile);
  if (needsRecovery) requestBigScreenFrameRecovery(tile, 'backlog-gap');
  return !needsRecovery;
}

function attachBigScreenTile(tile) {
  if (!tile || tile.socket) return;
  ensureBigScreenTileTerminal(tile);
  const socket = createTerminalSocket();
  tile.socket = socket;

  const attach = (force = false) => {
    const summary = tile.summary;
    if (!summary || !socket.connected) return false;
    const viewOnly = !tile.interactive;
    if (!force && tile.attachViewOnly === viewOnly && tile.attachSeq > 0) return false;
    tile.inputReady = false;
    tile.attachViewOnly = viewOnly;
    const attachSeq = beginBigScreenAttach(tile);
    socket.emit(TERMINAL_SOCKET_EVENTS.attach, {
      attach_seq: attachSeq,
      workspace_id: summary.workspace_id,
      terminal_id: summary.id || DEFAULT_TERMINAL,
      cols: tile.term?.cols || 80,
      rows: tile.term?.rows || 24,
      view_only: viewOnly,
    });
    return true;
  };
  tile.requestAttach = attach;

  socket.on(TERMINAL_SOCKET_EVENTS.connect, () => attach(true));
  socket.on(TERMINAL_SOCKET_EVENTS.ready, data => {
    const workspaceId = data.workspace_id;
    const terminalId = data.terminal_id || data.terminal?.id || DEFAULT_TERMINAL;
    if (workspaceId !== tile.summary.workspace_id || terminalId !== tile.summary.id) return;
    if (data.attach_seq !== undefined && Number(data.attach_seq) !== tile.attachSeq) return;
    tile.inputReady = tile.interactive && !tile.attachViewOnly;
    flushBigScreenTerminalInput(tile);
    restoreBigScreenTile(tile, data);
  });
  socket.on(TERMINAL_SOCKET_EVENTS.output, data => {
    const workspaceId = data.workspace_id;
    const terminalId = data.terminal_id || DEFAULT_TERMINAL;
    if (workspaceId !== tile.summary.workspace_id || terminalId !== tile.summary.id) return;
    acceptBigScreenDelta(tile, data);
  });
  socket.on(TERMINAL_SOCKET_EVENTS.frame, data => {
    const workspaceId = data.workspace_id;
    const terminalId = data.terminal_id || DEFAULT_TERMINAL;
    if (workspaceId !== tile.summary.workspace_id || terminalId !== tile.summary.id) return;
    if (tile.syncState === 'live' && (!data.stream_id || data.stream_id === tile.frameStreamId)) return;
    applyBigScreenFrame(tile, data, { authoritative: true, reason: 'server-frame' });
  });
  socket.on(TERMINAL_SOCKET_EVENTS.exit, data => {
    const workspaceId = data.workspace_id;
    const terminalId = data.terminal_id || DEFAULT_TERMINAL;
    if (workspaceId !== tile.summary.workspace_id || terminalId !== tile.summary.id) return;
    if (data.stream_id && tile.frameStreamId && data.stream_id !== tile.frameStreamId) return;
    releaseBigScreenSurface(tile);
    setBigScreenTileConnectionStatus(tile, '已退出');
  });
  socket.on(TERMINAL_SOCKET_EVENTS.error, data => {
    tile.inputReady = false;
    releaseBigScreenSurface(tile);
    setBigScreenTileConnectionStatus(tile, data.error || '连接失败');
  });
  socket.on(TERMINAL_SOCKET_EVENTS.connectError, () => {
    tile.inputReady = false;
    releaseBigScreenSurface(tile);
    setBigScreenTileConnectionStatus(tile, '连接失败');
  });
  socket.on(TERMINAL_SOCKET_EVENTS.disconnect, () => {
    tile.inputReady = false;
  });
}

function writeBigScreenChunks(tile, value, replaySeq) {
  const text = String(value || '');
  if (!text) return Promise.resolve(true);
  return new Promise(resolve => {
    let offset = 0;
    const writeNext = () => {
      if (!tile.term || tile.replaySeq !== replaySeq) {
        resolve(false);
        return;
      }
      const chunk = text.slice(offset, offset + TERMINAL_REPLAY_CHUNK_CHARS);
      offset += chunk.length;
      try {
        tile.term.write(chunk, () => {
          if (tile.replaySeq !== replaySeq) {
            resolve(false);
          } else if (offset >= text.length) {
            resolve(true);
          } else {
            setTimeout(writeNext, 0);
          }
        });
      } catch(e) {
        resolve(false);
      }
    };
    writeNext();
  });
}

async function restoreBigScreenTile(tile, data) {
  if (!tile?.term) return;
  preserveBigScreenSurface(tile);
  const authoritativeFrame = normalizeBigScreenFrame(data);
  if (data.screen_frame_supported && authoritativeFrame?.deltaSafe) {
    applyBigScreenFrame(tile, data, { authoritative: true });
    return;
  }
  if (data.screen_frame_supported) {
    tile.frameMode = true;
    tile.frameStreamId = authoritativeFrame?.streamId || String(data.stream_id || '');
    tile.frameStreamEpoch = authoritativeFrame?.streamEpoch
      || Math.max(0, Math.floor(Number(data.stream_epoch || 0)));
    tile.frameAppliedSeq = -1;
    tile.frameAppliedEnd = 0;
    tile.deltaAcceptedSeq = -1;
    tile.deltaAcceptedEnd = 0;
    tile.syncState = 'recovering';
    tile.replaying = true;
    requestBigScreenFrameRecovery(tile, 'attach-frame-unsafe');
    return;
  }
  const queuedBeforeReady = tile.deltaBacklog.map(delta => delta.data).join('');
  tile.deltaBacklog = [];
  tile.deltaQueue = [];
  tile.syncState = 'legacy';
  tile.frameMode = false;
  const replaySeq = ++tile.replaySeq;
  tile.replaying = true;
  tile.pendingOutput = '';
  resetBigScreenTerminal(tile);

  const snapshot = data.screen_frame_supported && !authoritativeFrame?.deltaSafe
    ? ''
    : String(data.screen_snapshot || '');
  const snapshotEnd = Math.floor(Number(data.screen_snapshot_end || 0));
  const historyEnd = Math.floor(Number(data.history_end ?? data.buffer_end ?? 0));
  if (snapshot && snapshotEnd <= historyEnd) {
    const targetCols = Math.max(2, Number(tile.term.cols || 80));
    const targetRows = Math.max(1, Number(tile.term.rows || 24));
    const snapshotCols = Math.max(2, Number(data.screen_snapshot_cols || targetCols));
    const snapshotRows = Math.max(1, Number(data.screen_snapshot_rows || targetRows));
    try { tile.term.resize(snapshotCols, snapshotRows); } catch(e) {}
    if (!await writeBigScreenChunks(tile, snapshot, replaySeq)) return;
    try { tile.term.resize(targetCols, targetRows); } catch(e) {}

    let delta = '';
    if (snapshotEnd < historyEnd && typeof fetchTerminalHistoryRange === 'function') {
      try {
        delta = await fetchTerminalHistoryRange({
          workspaceId: tile.summary.workspace_id,
          terminalId: tile.summary.id || DEFAULT_TERMINAL,
        }, snapshotEnd, historyEnd);
      } catch(e) {
        delta = '';
      }
    }
    if (snapshotEnd === historyEnd || delta) {
      if (!await writeBigScreenChunks(tile, delta, replaySeq)) return;
    } else {
      resetBigScreenTerminal(tile);
      if (!await writeBigScreenChunks(tile, data.buffer || '', replaySeq)) return;
    }
  } else {
    if (!await writeBigScreenChunks(tile, data.buffer || '', replaySeq)) return;
  }

  if (tile.replaySeq !== replaySeq) return;
  let queued = queuedBeforeReady;
  while (queued || tile.pendingOutput) {
    const pending = queued + tile.pendingOutput;
    queued = '';
    tile.pendingOutput = '';
    if (!await writeBigScreenChunks(tile, pending, replaySeq)) return;
  }
  tile.replaying = false;
  flushBigScreenTerminalInput(tile);
  fitBigScreenTileVisual(tile);
  try { tile.term.scrollToBottom(); } catch(e) {}
  releaseBigScreenSurface(tile);
}

function disposeBigScreenTile(key, tile = bigScreenTiles.get(key)) {
  if (!tile) return;
  tile.replaySeq += 1;
  tile.replaying = false;
  tile.pendingOutput = '';
  tile.pendingInput = '';
  tile.inputReady = false;
  tile.requestAttach = null;
  try { tile.frameFetchAbort?.abort(); } catch(e) {}
  tile.frameFetchAbort = null;
  tile.frameGeneration += 1;
  tile.syncState = 'disposed';
  tile.deltaQueue = [];
  tile.deltaBacklog = [];
  tile.deltaSignatures.clear();
  tile.deltaWriting = null;
  tile.pendingFrame = null;
  if (tile.frameRecoveryTimer) clearTimeout(tile.frameRecoveryTimer);
  tile.frameRecoveryTimer = 0;
  releaseBigScreenSurface(tile);
  try { tile.socket?.disconnect(); } catch(e) {}
  try { tile.resizeObserver?.disconnect(); } catch(e) {}
  try { tile.inputDisposable?.dispose(); } catch(e) {}
  try { tile.term?.dispose(); } catch(e) {}
  tile.el?.remove();
  bigScreenTiles.delete(key);
}

function disposeBigScreenTiles() {
  for (const [key, tile] of Array.from(bigScreenTiles.entries())) {
    disposeBigScreenTile(key, tile);
  }
  document.getElementById('bigScreenGrid')?.replaceChildren();
  bigScreenFocusedKey = '';
  updateBigScreenFocusUi();
}

function orderedBigScreenTiles() {
  return Array.from(bigScreenTiles.values())
    .filter(tile => tile?.el?.isConnected)
    .sort((a, b) => Number(a.el.style.order || 0) - Number(b.el.style.order || 0));
}

function updateBigScreenFocusUi() {
  const grid = document.getElementById('bigScreenGrid');
  const focusActions = document.getElementById('bigScreenFocusActions');
  const focusedTile = bigScreenFocusedKey ? bigScreenTiles.get(bigScreenFocusedKey) : null;
  const focused = Boolean(focusedTile?.el?.isConnected);
  if (!focused) bigScreenFocusedKey = '';

  grid?.classList.toggle('focused', focused);
  for (const tile of bigScreenTiles.values()) {
    const isFocused = focused && tile.key === bigScreenFocusedKey;
    tile.el?.classList.toggle('focused', isFocused);
    if (tile.el) {
      tile.el.tabIndex = isFocused ? -1 : 0;
      tile.el.setAttribute('role', isFocused ? 'group' : 'button');
      if (isFocused) tile.el.removeAttribute('aria-pressed');
      else tile.el.setAttribute('aria-pressed', 'false');
    }
    updateBigScreenTileInteraction(tile, isFocused);
  }
  if (focusActions) focusActions.hidden = !focused;

  const terminalCount = orderedBigScreenTiles().length;
  for (const id of ['bigScreenPreviousBtn', 'bigScreenNextBtn']) {
    const button = document.getElementById(id);
    if (button) button.disabled = terminalCount < 2;
  }
}

function setBigScreenFocus(key = '') {
  const nextKey = String(key || '');
  bigScreenFocusedKey = nextKey && bigScreenTiles.has(nextKey) ? nextKey : '';
  const nextTile = bigScreenTiles.get(bigScreenFocusedKey);
  if (nextTile && typeof markCodexCompletionSeen === 'function') {
    markCodexCompletionSeen(nextTile.summary.workspace_id, nextTile.summary.id || DEFAULT_TERMINAL);
  }
  updateBigScreenFocusUi();
  const focusedTile = bigScreenTiles.get(bigScreenFocusedKey);
  const refit = () => {
    for (const tile of bigScreenTiles.values()) fitBigScreenTileVisual(tile);
  };
  setTimeout(refit, 0);
  setTimeout(refit, 80);
  if (!focusedTile) return;
  const focusTerminal = () => {
    if (focusedTile.key !== bigScreenFocusedKey || !focusedTile.interactive) return;
    try { focusedTile.term?.focus(); } catch(e) {}
  };
  setTimeout(focusTerminal, 0);
  setTimeout(focusTerminal, 80);
}

function cycleBigScreenFocus(direction) {
  const tiles = orderedBigScreenTiles();
  if (!tiles.length) {
    setBigScreenFocus('');
    return;
  }
  const currentIndex = tiles.findIndex(tile => tile.key === bigScreenFocusedKey);
  const step = Number(direction) < 0 ? -1 : 1;
  const nextIndex = currentIndex < 0
    ? (step < 0 ? tiles.length - 1 : 0)
    : (currentIndex + step + tiles.length) % tiles.length;
  setBigScreenFocus(tiles[nextIndex].key);
}

function renderBigScreenTiles(terminals, emptyText = '暂无可用终端') {
  const grid = document.getElementById('bigScreenGrid');
  if (!grid) return;
  let layoutChanged = false;
  const previousFocusedIndex = orderedBigScreenTiles()
    .findIndex(tile => tile.key === bigScreenFocusedKey);
  const nextKeys = new Set(terminals.map(bigScreenTerminalKey));
  for (const [key, tile] of Array.from(bigScreenTiles.entries())) {
    if (!nextKeys.has(key)) {
      disposeBigScreenTile(key, tile);
      layoutChanged = true;
    }
  }

  for (let index = 0; index < terminals.length; index += 1) {
    const summary = terminals[index];
    const key = bigScreenTerminalKey(summary);
    let tile = bigScreenTiles.get(key);
    if (!tile) {
      tile = createBigScreenTile(summary);
      bigScreenTiles.set(key, tile);
      layoutChanged = true;
    }
    updateBigScreenTile(tile, summary);
    if (tile.el.parentNode !== grid) {
      grid.appendChild(tile.el);
      layoutChanged = true;
    }
    const order = String(index);
    if (tile.el.style.order !== order) tile.el.style.order = order;
    ensureBigScreenTileTerminal(tile);
    attachBigScreenTile(tile);
  }

  const placeholderCount = Math.max(0, BIG_SCREEN_LIMIT - terminals.length);
  const placeholders = Array.from(grid.querySelectorAll(':scope > .big-screen-placeholder'));
  while (placeholders.length > placeholderCount) {
    placeholders.pop().remove();
    layoutChanged = true;
  }
  while (placeholders.length < placeholderCount) {
    const placeholder = document.createElement('div');
    placeholder.className = 'big-screen-placeholder';
    placeholders.push(placeholder);
    layoutChanged = true;
  }
  for (let index = 0; index < placeholders.length; index += 1) {
    const placeholder = placeholders[index];
    const text = terminals.length || index ? '' : emptyText;
    if (placeholder.textContent !== text) placeholder.textContent = text;
    if (placeholder.parentNode !== grid) {
      grid.appendChild(placeholder);
      layoutChanged = true;
    }
    const order = String(terminals.length + index);
    if (placeholder.style.order !== order) placeholder.style.order = order;
  }

  if (bigScreenFocusedKey && !bigScreenTiles.has(bigScreenFocusedKey)) {
    const remainingTiles = orderedBigScreenTiles();
    const fallbackIndex = Math.min(Math.max(previousFocusedIndex, 0), remainingTiles.length - 1);
    bigScreenFocusedKey = remainingTiles[fallbackIndex]?.key || '';
  }
  updateBigScreenFocusUi();

  if (layoutChanged) {
    setTimeout(() => {
      for (const tile of bigScreenTiles.values()) {
        fitBigScreenTileVisual(tile);
      }
    }, 60);
  }
}

function renderBigScreenLoading(text = '检测中...') {
  const grid = document.getElementById('bigScreenGrid');
  if (!grid || grid.children.length) return;
  renderBigScreenTiles([], text);
}

function updateBigScreenFromStats(data = {}) {
  if (!terminalFullscreen) return;
  const view = bigScreenTerminalViewFromStats(data);
  renderBigScreenTiles(view.terminals, '暂无可用终端');
  const summary = document.getElementById('bigScreenSummary');
  if (summary) {
    const time = new Date().toLocaleTimeString([], { hour:'2-digit', minute:'2-digit', second:'2-digit' });
    const prefix = view.fallback ? '暂无活跃，显示可用' : '活跃';
    summary.textContent = prefix + ' ' + view.terminals.length + '/' + BIG_SCREEN_LIMIT + ' · 可用终端 ' + view.totalUsable + ' · ' + time;
  }
}

async function refreshBigScreenTerminals() {
  if (!terminalFullscreen) return null;
  const summary = document.getElementById('bigScreenSummary');
  if (summary) summary.textContent = '检测中...';
  renderBigScreenLoading();
  const data = await refreshStats();
  if (!data && summary) summary.textContent = '检测失败';
  return data;
}

function startBigScreenRefresh() {
  stopBigScreenRefresh();
  renderBigScreenLoading();
  startStatsRefresh();
  if (!statsRefreshInFlight) refreshStats();
}

function stopBigScreenRefresh() {
  if (bigScreenTimer) clearInterval(bigScreenTimer);
  bigScreenTimer = null;
}

async function setTerminalFullscreen(enabled) {
  if (fullscreenTransitionPending) return false;
  setFullscreenTransitionPending(true);
  const shouldEnable = Boolean(enabled);
  try {
    if (shouldEnable) {
      updatePageFullscreenUi(false);
      showTerminalArea();
      const fullscreenTarget = terminalFullscreenElement();
      const currentFullscreen = browserFullscreenElement();
      if (currentFullscreen && currentFullscreen !== fullscreenTarget) {
        try {
          await exitBrowserFullscreen();
        } catch(e) {
          console.warn('exit fullscreen before big screen failed', e);
        }
      }
      updateTerminalFullscreenUi(true);
      startBigScreenRefresh();
      if (fullscreenTarget && browserFullscreenElement() !== fullscreenTarget) {
        try {
          await requestBrowserFullscreen(fullscreenTarget);
        } catch(e) {
          console.warn('browser fullscreen failed', e);
          toast('浏览器全屏失败，已使用页面大屏', 'error');
        }
      }
      return true;
    }
    if (browserFullscreenElement()) {
      try {
        await exitBrowserFullscreen();
      } catch(e) {
        console.warn('exit fullscreen failed', e);
      }
    }
    stopBigScreenRefresh();
    disposeBigScreenTiles();
    updateTerminalFullscreenUi(false);
    syncMainTerminalAfterFullscreen();
    return true;
  } finally {
    setFullscreenTransitionPending(false);
  }
}

async function setPageFullscreen(enabled) {
  if (fullscreenTransitionPending) return false;
  setFullscreenTransitionPending(true);
  const shouldEnable = Boolean(enabled);
  try {
    if (shouldEnable) {
      if (terminalFullscreen) {
        stopBigScreenRefresh();
        disposeBigScreenTiles();
        updateTerminalFullscreenUi(false);
      }
      const fullscreenTarget = pageFullscreenElement();
      const currentFullscreen = browserFullscreenElement();
      if (currentFullscreen && currentFullscreen !== fullscreenTarget) {
        try {
          await exitBrowserFullscreen();
        } catch(e) {
          console.warn('exit fullscreen before page fullscreen failed', e);
        }
      }
      updatePageFullscreenUi(true);
      if (fullscreenTarget && browserFullscreenElement() !== fullscreenTarget) {
        try {
          await requestBrowserFullscreen(fullscreenTarget);
        } catch(e) {
          updatePageFullscreenUi(false);
          console.warn('page fullscreen failed', e);
          toast('页面全屏失败', 'error');
          return false;
        }
      }
      syncMainTerminalAfterFullscreen();
      return true;
    }
    updatePageFullscreenUi(false);
    if (browserFullscreenElement()) {
      try {
        await exitBrowserFullscreen();
      } catch(e) {
        console.warn('exit page fullscreen failed', e);
      }
    }
    syncMainTerminalAfterFullscreen();
    return true;
  } finally {
    setFullscreenTransitionPending(false);
  }
}

function togglePageFullscreen() {
  setPageFullscreen(!pageFullscreen);
}

function toggleTerminalFullscreen() {
  setTerminalFullscreen(!terminalFullscreen);
}

function handleBrowserFullscreenChange() {
  markFullscreenLayoutTransition();
  const current = browserFullscreenElement();
  if (terminalFullscreen && current !== terminalFullscreenElement()) {
    stopBigScreenRefresh();
    disposeBigScreenTiles();
    updateTerminalFullscreenUi(false);
  }
  if (pageFullscreen && current !== pageFullscreenElement()) {
    updatePageFullscreenUi(false);
  }
  if (!terminalFullscreen) syncMainTerminalAfterFullscreen();
}

document.addEventListener('fullscreenchange', handleBrowserFullscreenChange);
document.addEventListener('webkitfullscreenchange', handleBrowserFullscreenChange);
