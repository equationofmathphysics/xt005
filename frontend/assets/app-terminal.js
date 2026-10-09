// ===================== 终端 =====================

let term = null;
let termFit = null;
let termSocket = null;
let termInitialized = false;
let termConnected = false;
let terminalAttachSeq = 0;
let activeAttachSeq = 0;
let terminalViews = new Map();
let terminalLayoutFrame = 0;
let terminalLayoutPending = null;
let terminalLayoutDeferred = null;
let terminalLayoutDeferredTimer = 0;
let terminalLastGeometryEmission = '';
let terminalLastGeometryClaimAt = 0;
let codexHistoryLoaded = false;
const PENDING_TERMINAL_OUTPUT_LIMIT = 16777216;
const TERMINAL_ATTACH_REPLAY_CHARS = 49152;
const TERMINAL_HISTORY_PAGE_CHARS = 131072;
const TERMINAL_CLIENT_REPLAY_WINDOW_CHARS = 262144;
const TERMINAL_CLIENT_HISTORY_WINDOW_CHARS = 1048576;
const TERMINAL_REPLAY_CHUNK_CHARS = 32768;
const TERMINAL_FALLBACK_TEXT_CHARS = 120000;
const TERMINAL_HISTORY_SCROLL_THRESHOLD = 3;
const TERMINAL_VIEW_CACHE_LIMIT = 12;
const TERMINAL_BOTTOM_FOLLOW_THRESHOLD = 2;
const TERMINAL_HISTORY_FETCH_MAX_CHARS = 16777216;
const TERMINAL_SNAPSHOT_MAX_CHARS = 7500000;
const TERMINAL_SNAPSHOT_DEBOUNCE_MS = 450;
const TERMINAL_FRAME_RECOVERY_RETRY_MS = 500;
const TERMINAL_USER_INPUT_BG = '\x1b[48;5;238m';
const TERMINAL_USER_INPUT_BG_END = '\x1b[49m';
const TERMINAL_EMPTY_VIEW_TEXT = '没有打开的终端。点击“+ 终端”新建一个会话。';
const TERMINAL_GEOMETRY_CLAIM_INTERVAL_MS = 1500;

function scheduleTerminalPromptNavigation(view = activeTerminalView(), options = {}) {
  if (typeof window.scheduleTerminalPromptNavigatorRefresh !== 'function') return;
  window.scheduleTerminalPromptNavigatorRefresh(view, options);
}

function resetTerminalPromptNavigation(view = activeTerminalView()) {
  if (typeof window.resetTerminalPromptNavigator !== 'function') return;
  window.resetTerminalPromptNavigator(view);
}

function hideTerminalPromptNavigation() {
  if (typeof window.hideTerminalPromptNavigator !== 'function') return;
  window.hideTerminalPromptNavigator();
}

function markTerminalPromptSubmissionScan(view, data) {
  if (!view || !/[\r\n]/.test(String(data || ''))) return;
  // A submitted line causes one scan after xterm parses the resulting update.
  view.promptNavScanAfterWrite = true;
}

function consumeTerminalPromptSubmissionScan(view) {
  if (!view?.promptNavScanAfterWrite) return false;
  view.promptNavScanAfterWrite = false;
  scheduleTerminalPromptNavigation(view, { scan: true });
  return true;
}

function terminalViewKey(workspaceId = activeWorkspaceId, terminalId = activeTerminalId) {
  const resolvedTerminal = terminalId === '' ? '' : (terminalId || DEFAULT_TERMINAL);
  return (workspaceId || DEFAULT_WORKSPACE_ID) + ':' + resolvedTerminal;
}

function isActiveTerminalView(view) {
  return Boolean(
    view
    && !view.disposed
    && terminalViews.get(view.key) === view
    && view.key === terminalViewKey()
  );
}

function activeTerminalView() {
  return terminalViews.get(terminalViewKey()) || null;
}

function createTerminalView(workspaceId = activeWorkspaceId, terminalId = activeTerminalId) {
  const key = terminalViewKey(workspaceId, terminalId);
  const existing = terminalViews.get(key);
  if (existing) return existing;

  const container = document.getElementById('terminalContainer');
  const mount = document.createElement('div');
  mount.className = 'terminal-view';
  mount.dataset.terminalKey = key;
  if (key === terminalViewKey()) mount.classList.add('active');
  container.appendChild(mount);

  const viewTerm = new Terminal({
    cursorBlink: true,
    fontSize: 14,
    scrollback: TERMINAL_SCROLLBACK_LINES,
    convertEol: false,
    windowsMode: false,
    allowProposedApi: false,
    fontFamily: '"JetBrains Mono", "Fira Code", "Consolas", monospace',
    theme: TERMINAL_THEME,
  });
  const viewFit = new FitAddon.FitAddon();
  const viewSerialize = typeof window.SerializeAddon?.SerializeAddon === 'function'
    ? new window.SerializeAddon.SerializeAddon()
    : null;
  viewTerm.loadAddon(viewFit);
  if (viewSerialize) viewTerm.loadAddon(viewSerialize);
  viewTerm.open(mount);

  const view = {
    key,
    disposed: false,
    workspaceId,
    terminalId,
    mount,
    term: viewTerm,
    fit: viewFit,
    serialize: viewSerialize,
    replaySeq: 0,
    replaying: false,
    hydrated: false,
    bufferVersion: '',
    pendingOutput: '',
    pendingInput: '',
    acceptingQueuedInput: false,
    replayBuffer: '',
    replayUnits: 0,
    replayStart: 0,
    replayEnd: 0,
    renderedEnd: 0,
    historyStart: 0,
    historyEnd: 0,
    historyLoading: false,
    historyExhausted: true,
    historyLoadSeq: 0,
    historyResumeReconcileTimer: 0,
    historyResumeReconcilePending: false,
    followOutput: true,
    followScrollFrame: 0,
    followScrollTimer: 0,
    followScrollForce: false,
    outputAtLineStart: true,
    outputUserInputHighlightOpen: false,
    outputUserInputContinuation: false,
    checkpointSupported: false,
    snapshotTimer: 0,
    snapshotInFlight: null,
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
    frameRefreshTimer: 0,
    frameOverlay: null,
    deltaQueue: [],
    deltaBacklog: [],
    deltaWriting: null,
    deltaAcceptedEnd: 0,
    deltaAcceptedSeq: -1,
    deltaPaused: true,
    deltaRecoveryAttempts: 0,
    deltaRecoveryReason: '',
    deltaControlTail: '',
    deltaSignatures: new Map(),
    attaching: false,
    attachDirty: false,
    preReadyFrame: null,
    activationFrame: 0,
    promptNavWriteDisposable: null,
    promptNavResizeDisposable: null,
    promptNavScanAfterWrite: false,
    lastActiveAt: Date.now(),
  };
  terminalViews.set(key, view);

  viewTerm.onData(data => {
    if (!isActiveTerminalView(view)) return;
    if (Date.now() - terminalLastGeometryClaimAt >= TERMINAL_GEOMETRY_CLAIM_INTERVAL_MS) {
      scheduleTerminalLayoutSync({ claimGeometry: true });
    }
    emitTerminalInput(data, 'xterm');
  });
  view.historyScrollDisposable = viewTerm.onScroll(() => handleTerminalScroll(view));
  if (typeof viewTerm.onWriteParsed === 'function') {
    view.promptNavWriteDisposable = viewTerm.onWriteParsed(() => {
      consumeTerminalPromptSubmissionScan(view);
    });
  }
  if (typeof viewTerm.onResize === 'function') {
    view.promptNavResizeDisposable = viewTerm.onResize(() => {
      if (!view.hydrated || view.replaying || view.attaching) return;
      scheduleTerminalPromptNavigation(view, { scan: true, force: true });
    });
  }
  mount.addEventListener('wheel', event => markTerminalWheelIntent(view, event), { passive: true });
  mount.addEventListener('pointerdown', () => {
    if (
      isActiveTerminalView(view)
      && Date.now() - terminalLastGeometryClaimAt >= TERMINAL_GEOMETRY_CLAIM_INTERVAL_MS
    ) {
      scheduleTerminalLayoutSync({ claimGeometry: true });
    }
  });
  installTerminalImeFallback(mount);
  pruneTerminalViews();
  return view;
}

function activateTerminalView(workspaceId = activeWorkspaceId, terminalId = activeTerminalId) {
  if (!workspaceId || !terminalId) {
    deactivateTerminalViews();
    return null;
  }
  const view = createTerminalView(workspaceId, terminalId);
  for (const item of terminalViews.values()) {
    item.mount.classList.toggle('active', item.key === view.key);
  }
  hideTerminalEmptyState();
  term = view.term;
  termFit = view.fit;
  view.lastActiveAt = Date.now();
  pruneTerminalViews();
  const fitView = () => {
    fitTerminalViewNow(view);
    refreshTerminalViewSurface(view);
    try { view.term.focus(); } catch(e) {}
  };
  if (typeof requestAnimationFrame === 'function') {
    if (view.activationFrame) cancelAnimationFrame(view.activationFrame);
    view.activationFrame = requestAnimationFrame(() => {
      view.activationFrame = 0;
      fitView();
    });
  } else {
    fitView();
  }
  if (view.hydrated || view.promptNavigatorState) {
    scheduleTerminalPromptNavigation(view, { scan: true });
  } else {
    hideTerminalPromptNavigation();
  }
  return view;
}

function fitTerminalViewNow(view = activeTerminalView()) {
  if (!view?.fit || !view?.term || !view.mount) return false;
  const rect = view.mount.getBoundingClientRect();
  if (!rect || rect.width < 80 || rect.height < 40) return false;
  try {
    view.fit.fit();
    return true;
  } catch(e) {
    return false;
  }
}

function refreshTerminalViewSurface(view = activeTerminalView()) {
  if (!view?.term || view.disposed) return false;
  try {
    view.term.refresh(0, Math.max(0, Number(view.term.rows || 1) - 1));
    return true;
  } catch(e) {
    return false;
  }
}

function preserveTerminalFrameSurface(view = activeTerminalView()) {
  if (!view?.mount || view.disposed) return false;
  if (view.frameOverlay?.isConnected) return true;
  const overlay = captureTerminalSurface(view.mount, TERMINAL_THEME.background || '#0d1117');
  if (!overlay) {
    const cover = document.createElement('div');
    cover.className = 'terminal-frame-overlay';
    cover.setAttribute('aria-hidden', 'true');
    cover.style.background = TERMINAL_THEME.background || '#0d1117';
    view.mount.appendChild(cover);
    view.frameOverlay = cover;
    return true;
  }
  view.mount.appendChild(overlay);
  view.frameOverlay = overlay;
  return true;
}

function releaseTerminalFrameSurface(view = activeTerminalView()) {
  const overlay = view?.frameOverlay;
  if (!overlay) return;
  view.frameOverlay = null;
  try { overlay.remove(); } catch(e) {}
}

function terminalCanClaimGeometry() {
  const workArea = document.getElementById('terminalWorkArea');
  const documentFocused = typeof document.hasFocus !== 'function' || document.hasFocus();
  return Boolean(
    activeWorkspaceId
    && activeTerminalId
    && !terminalFullscreen
    && document.visibilityState === 'visible'
    && documentFocused
    && workArea
    && workArea.style.display !== 'none'
  );
}

function mergeTerminalLayoutOptions(current, next = {}) {
  const base = current || {
    emit: false,
    syncWorkspace: false,
    claimGeometry: false,
    forceEmit: false,
    focus: false,
  };
  return {
    emit: base.emit || next.emit !== false,
    syncWorkspace: base.syncWorkspace || next.syncWorkspace === true,
    claimGeometry: base.claimGeometry || Boolean(next.claimGeometry),
    forceEmit: base.forceEmit || Boolean(next.forceEmit),
    focus: base.focus || Boolean(next.focus),
  };
}

function fitActiveTerminalView() {
  const view = activeTerminalView();
  fitTerminalViewNow(view);
  return view;
}

function syncTerminalLayoutNow(options = {}) {
  const view = fitActiveTerminalView();
  if (!view?.term || !isActiveTerminalView(view)) return false;

  term = view.term;
  termFit = view.fit;
  if (options.focus) {
    try { view.term.focus(); } catch(e) {}
  }
  const fullscreenSettleDelay = typeof fullscreenLayoutSettleDelay === 'function'
    ? fullscreenLayoutSettleDelay()
    : 0;
  if (options.emit !== false && fullscreenSettleDelay > 0) {
    terminalLayoutDeferred = mergeTerminalLayoutOptions(terminalLayoutDeferred, options);
    if (terminalLayoutDeferredTimer) clearTimeout(terminalLayoutDeferredTimer);
    terminalLayoutDeferredTimer = setTimeout(() => {
      terminalLayoutDeferredTimer = 0;
      const deferred = terminalLayoutDeferred || {};
      terminalLayoutDeferred = null;
      queueTerminalLayoutFrame(deferred);
    }, Math.ceil(fullscreenSettleDelay) + 1);
    return true;
  }
  if (options.emit === false || !termSocket?.connected || !termConnected) return true;
  if (!terminalCanClaimGeometry()) return true;

  const cols = Math.max(2, Number(view.term.cols || 80));
  const rows = Math.max(1, Number(view.term.rows || 24));
  const syncWorkspace = options.syncWorkspace !== false;
  const eventName = syncWorkspace
    ? TERMINAL_SOCKET_EVENTS.resizeWorkspace
    : TERMINAL_SOCKET_EVENTS.resize;
  const emissionKey = [termSocket.id || '', activeWorkspaceId, eventName, cols, rows].join(':');
  const now = Date.now();
  const claimGeometry = Boolean(
    options.claimGeometry
    && (options.forceEmit || now - terminalLastGeometryClaimAt >= TERMINAL_GEOMETRY_CLAIM_INTERVAL_MS)
  );
  if (!options.forceEmit && !claimGeometry && emissionKey === terminalLastGeometryEmission) return true;

  termSocket.emit(eventName, {
    attach_seq: activeAttachSeq,
    cols,
    rows,
    claim_geometry: claimGeometry,
  });
  terminalLastGeometryEmission = emissionKey;
  if (claimGeometry) terminalLastGeometryClaimAt = now;
  return true;
}

function queueTerminalLayoutFrame(options = {}) {
  terminalLayoutPending = mergeTerminalLayoutOptions(terminalLayoutPending, options);
  if (terminalLayoutFrame) return;
  const run = () => {
    terminalLayoutFrame = 0;
    const pending = terminalLayoutPending || {};
    terminalLayoutPending = null;
    syncTerminalLayoutNow(pending);
  };
  if (typeof requestAnimationFrame !== 'function') {
    terminalLayoutFrame = setTimeout(run, 0);
    return;
  }
  terminalLayoutFrame = requestAnimationFrame(() => {
    terminalLayoutFrame = requestAnimationFrame(run);
  });
}

function scheduleTerminalLayoutSync(options = {}) {
  const requested = {
    emit: options.emit !== false,
    syncWorkspace: options.syncWorkspace === true,
    claimGeometry: Boolean(options.claimGeometry),
    forceEmit: Boolean(options.forceEmit),
    focus: Boolean(options.focus),
  };
  queueTerminalLayoutFrame(requested);
}

function disposeTerminalView(workspaceId, terminalId) {
  const key = terminalViewKey(workspaceId, terminalId);
  const view = terminalViews.get(key);
  if (!view) return;
  const viewTerm = view.term;
  const viewMount = view.mount;
  const wasActive = term === viewTerm;
  terminalViews.delete(key);
  view.disposed = true;
  if (view.snapshotTimer) clearTimeout(view.snapshotTimer);
  if (view.frameRefreshTimer) clearTimeout(view.frameRefreshTimer);
  try { view.frameFetchAbort?.abort(); } catch(e) {}
  view.frameFetchAbort = null;
  if (view.followScrollTimer) clearTimeout(view.followScrollTimer);
  if (view.historyResumeReconcileTimer) clearTimeout(view.historyResumeReconcileTimer);
  view.historyResumeReconcileTimer = 0;
  view.historyResumeReconcilePending = false;
  if (view.followScrollFrame && typeof cancelAnimationFrame === 'function') {
    cancelAnimationFrame(view.followScrollFrame);
  }
  if (view.activationFrame && typeof cancelAnimationFrame === 'function') {
    cancelAnimationFrame(view.activationFrame);
  }
  view.frameGeneration += 1;
  view.deltaPaused = true;
  view.deltaQueue = [];
  view.deltaBacklog = [];
  view.deltaSignatures.clear();
  settleTerminalFrameJob(view.frameWriting, false);
  settleTerminalFrameJob(view.pendingFrame, false);
  view.frameWriting = null;
  view.pendingFrame = null;
  releaseTerminalFrameSurface(view);
  view.pendingOutput = '';
  view.pendingInput = '';
  view.replayBuffer = '';
  view.preReadyFrame = null;
  view.frameFetchInFlight = null;
  view.snapshotInFlight = null;
  viewMount.classList.remove('active');
  viewMount.style.pointerEvents = 'none';
  try { view.historyScrollDisposable?.dispose(); } catch(e) {}
  try { view.promptNavWriteDisposable?.dispose(); } catch(e) {}
  try { view.promptNavResizeDisposable?.dispose(); } catch(e) {}
  view.historyScrollDisposable = null;
  view.promptNavWriteDisposable = null;
  view.promptNavResizeDisposable = null;
  view.promptNavScanAfterWrite = false;
  view.promptNavigatorState = null;
  view.term = null;
  view.fit = null;
  view.serialize = null;
  view.mount = null;
  const disposeView = () => {
    try { viewTerm.dispose(); } catch(e) {}
    try { viewMount.remove(); } catch(e) {}
  };
  setTimeout(disposeView, 80);
  if (wasActive) {
    term = null;
    termFit = null;
    hideTerminalPromptNavigation();
  }
}

function deactivateTerminalViews() {
  for (const item of terminalViews.values()) {
    item.mount.classList.remove('active');
  }
  term = null;
  termFit = null;
  hideTerminalPromptNavigation();
}

function terminalEmptyStateElement() {
  const container = document.getElementById('terminalContainer');
  if (!container) return null;
  let el = document.getElementById('terminalEmptyState');
  if (!el) {
    el = document.createElement('div');
    el.id = 'terminalEmptyState';
    el.className = 'terminal-empty-state';
    el.innerHTML = '<div class="terminal-empty-copy"><div class="terminal-empty-title"></div><div class="terminal-empty-detail"></div></div>';
    container.appendChild(el);
  }
  return el;
}

function hideTerminalEmptyState() {
  const el = document.getElementById('terminalEmptyState');
  if (!el) return;
  el.classList.remove('active');
  el.setAttribute('aria-hidden', 'true');
}

function showTerminalEmptyState(message = '没有终端', detail = '') {
  deactivateTerminalViews();
  activeAttachSeq = ++terminalAttachSeq;
  clearPendingTerminalInput();
  const el = terminalEmptyStateElement();
  if (!el) return;
  const title = el.querySelector('.terminal-empty-title');
  const body = el.querySelector('.terminal-empty-detail');
  if (title) title.textContent = message || '没有终端';
  if (body) body.textContent = detail || (activeWorkspaceId ? '点击“+ 终端”新建一个会话。' : '请先新增工作区。');
  el.classList.add('active');
  el.setAttribute('aria-hidden', 'false');
}

function pruneTerminalViews() {
  if (terminalViews.size <= TERMINAL_VIEW_CACHE_LIMIT) return;
  const activeKey = terminalViewKey();
  const candidates = Array.from(terminalViews.values())
    .filter(view => view.key !== activeKey && !view.pendingInput)
    .sort((a, b) => (a.lastActiveAt || 0) - (b.lastActiveAt || 0));
  while (terminalViews.size > TERMINAL_VIEW_CACHE_LIMIT && candidates.length) {
    const view = candidates.shift();
    disposeTerminalView(view.workspaceId, view.terminalId);
  }
}

function disposeWorkspaceTerminalViews(workspaceId) {
  for (const view of Array.from(terminalViews.values())) {
    if (view.workspaceId === workspaceId) disposeTerminalView(view.workspaceId, view.terminalId);
  }
}

function initTerminal() {
  if (termInitialized) {
    activateTerminalView();
    scheduleTerminalLayoutSync({
      claimGeometry: terminalCanClaimGeometry(),
      forceEmit: true,
      focus: true,
    });
    return;
  }
  termInitialized = true;
  activateTerminalView();

  const ro = new ResizeObserver(() => {
    scheduleTerminalLayoutSync({
      claimGeometry: terminalCanClaimGeometry(),
    });
  });
  ro.observe(document.getElementById('terminalContainer'));

  window.addEventListener('resize', () => {
    if (typeof markFullscreenLayoutTransition === 'function' && (
      fullscreenTransitionPending || pageFullscreen || terminalFullscreen
    )) markFullscreenLayoutTransition();
    scheduleTerminalLayoutSync({ claimGeometry: terminalCanClaimGeometry() });
  });
  window.addEventListener('focus', () => {
    scheduleTerminalLayoutSync({ claimGeometry: true, forceEmit: true });
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') return;
    scheduleTerminalLayoutSync({
      claimGeometry: terminalCanClaimGeometry(),
      forceEmit: true,
    });
    const refresh = () => refreshTerminalViewSurface(activeTerminalView());
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(refresh);
    else refresh();
  });
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', () => {
      if (typeof markFullscreenLayoutTransition === 'function' && (
        fullscreenTransitionPending || pageFullscreen || terminalFullscreen
      )) markFullscreenLayoutTransition();
      scheduleTerminalLayoutSync({ claimGeometry: terminalCanClaimGeometry() });
    });
  }
  if (document.fonts?.ready) {
    document.fonts.ready.then(() => {
      scheduleTerminalLayoutSync({
        claimGeometry: terminalCanClaimGeometry(),
      });
    });
  }
  connectTerminal();
}

function sanitizeTerminalReplayText(text) {
  return String(text || '')
    .replace(/\x1b\[[0-9;?=>]*[cnR]/g, '')
    .replace(/\x1b\](?:10|11);[^\x07\x1b]*(?:\x07|\x1b\\)/g, '');
}

function terminalTextUnitLength(text) {
  let length = 0;
  for (const _symbol of String(text || '')) length += 1;
  return length;
}

function terminalTextSliceIndex(text, units) {
  const value = String(text || '');
  const target = Math.max(0, Number(units || 0));
  if (!target) return 0;
  let consumed = 0;
  let index = 0;
  for (const symbol of value) {
    if (consumed >= target) break;
    consumed += 1;
    index += symbol.length;
  }
  return index;
}

function setTerminalReplayWindow(view, buffer, start = 0, end = null) {
  if (!view) return;
  const full = String(buffer || '');
  const fullStart = Number.isFinite(Number(start)) ? Number(start) : 0;
  const fullUnits = terminalTextUnitLength(full);
  const fullEnd = end === null || end === undefined ? fullStart + fullUnits : Number(end);
  const droppedUnits = Math.max(0, fullUnits - TERMINAL_CLIENT_REPLAY_WINDOW_CHARS);
  const trimmed = droppedUnits ? full.slice(terminalTextSliceIndex(full, droppedUnits)) : full;
  const trimmedUnits = fullUnits - droppedUnits;
  view.replayBuffer = trimmed;
  view.replayUnits = trimmedUnits;
  view.replayEnd = Number.isFinite(fullEnd) ? fullEnd : fullStart + fullUnits;
  view.replayStart = view.replayEnd - trimmedUnits;
}

function setTerminalReplayWindowFromStart(view, buffer, start = 0, limit = TERMINAL_CLIENT_HISTORY_WINDOW_CHARS) {
  if (!view) return;
  const full = String(buffer || '');
  const fullStart = Number.isFinite(Number(start)) ? Number(start) : 0;
  const fullUnits = terminalTextUnitLength(full);
  const trimmedUnits = Math.min(fullUnits, Math.max(0, Number(limit || 0)));
  const trimmed = full.slice(0, terminalTextSliceIndex(full, trimmedUnits));
  view.replayBuffer = trimmed;
  view.replayUnits = trimmedUnits;
  view.replayStart = fullStart;
  view.replayEnd = fullStart + trimmedUnits;
}

function serializeTerminalCheckpoint(view) {
  if (!view?.serialize || !view?.term) return '';
  let scrollback = Math.max(0, Number(view.term.options?.scrollback || TERMINAL_SCROLLBACK_LINES));
  while (true) {
    let snapshot = '';
    try {
      snapshot = view.serialize.serialize({ scrollback });
    } catch(e) {
      return '';
    }
    if (snapshot.length <= TERMINAL_SNAPSHOT_MAX_CHARS) return snapshot;
    if (!scrollback) return '';
    scrollback = Math.floor(scrollback / 2);
  }
}

async function publishTerminalSnapshot(view = activeTerminalView(), options = {}) {
  if (view?.snapshotTimer) {
    clearTimeout(view.snapshotTimer);
    view.snapshotTimer = 0;
  }
  if (
    view?.frameMode
    ||
    !view?.checkpointSupported
    || !view.serialize
    || !isActiveTerminalView(view)
    || !termSocket?.connected
    || !termConnected
    || !view.hydrated
    || view.replaying
    || Number(view.renderedEnd || 0) !== Number(view.replayEnd || 0)
  ) return false;

  if (view.snapshotInFlight) {
    if (!options.force) return view.snapshotInFlight;
    await view.snapshotInFlight;
    if (!isActiveTerminalView(view)) return false;
  }

  const snapshot = serializeTerminalCheckpoint(view);
  const snapshotEnd = Math.floor(Number(view.renderedEnd || 0));
  if (!snapshot || !Number.isFinite(snapshotEnd)) return false;

  const request = new Promise(resolve => {
    let settled = false;
    const finish = value => {
      if (settled) return;
      settled = true;
      resolve(Boolean(value));
    };
    const timeout = setTimeout(() => finish(false), 1800);
    termSocket.emit(TERMINAL_SOCKET_EVENTS.snapshot, {
      attach_seq: activeAttachSeq,
      snapshot,
      snapshot_end: snapshotEnd,
      cols: Math.max(2, Number(view.term.cols || 80)),
      rows: Math.max(1, Number(view.term.rows || 24)),
    }, response => {
      clearTimeout(timeout);
      finish(response?.ok);
    });
  });
  view.snapshotInFlight = request;
  try {
    return await request;
  } finally {
    if (view.snapshotInFlight === request) view.snapshotInFlight = null;
  }
}

function queueTerminalSnapshot(view = activeTerminalView(), delay = TERMINAL_SNAPSHOT_DEBOUNCE_MS) {
  if (!view?.checkpointSupported || !isActiveTerminalView(view)) return;
  if (view.snapshotTimer) clearTimeout(view.snapshotTimer);
  view.snapshotTimer = setTimeout(() => {
    view.snapshotTimer = 0;
    publishTerminalSnapshot(view);
  }, Math.max(0, Number(delay || 0)));
}

function normalizeTerminalScreenFrame(data = {}) {
  const snapshot = String(data.screen_snapshot || '');
  const streamId = String(data.stream_id || '');
  const streamEpoch = Math.max(0, Math.floor(Number(data.stream_epoch || 0)));
  const frameSeq = Math.floor(Number(data.frame_seq));
  const frameEnd = Math.floor(Number(
    data.frame_end ?? data.screen_snapshot_end ?? data.history_end ?? data.buffer_end
  ));
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

function normalizeTerminalDelta(data = {}) {
  const streamId = String(data.stream_id || '');
  const streamEpoch = Math.max(0, Math.floor(Number(data.stream_epoch || 0)));
  const frameSeq = Math.floor(Number(data.frame_seq));
  const start = Math.floor(Number(data.output_start));
  const end = Math.floor(Number(data.output_end));
  const eventType = data.event_type === 'resize' ? 'resize' : 'output';
  const rawCols = Number(data.cols);
  const rawRows = Number(data.rows);
  if (
    !streamId
    || !Number.isFinite(frameSeq)
    || !Number.isFinite(start)
    || !Number.isFinite(end)
    || end < start
  ) return null;
  const delta = {
    streamId,
    streamEpoch,
    frameSeq,
    start,
    end,
    eventType,
    text: String(data.data || ''),
    historyStart: Math.max(0, Math.floor(Number(data.history_start || 0))),
    cols: Math.max(2, Math.floor(rawCols || 0)),
    rows: Math.max(1, Math.floor(rawRows || 0)),
  };
  if (
    eventType === 'resize'
    && (!Number.isFinite(rawCols) || rawCols <= 0 || !Number.isFinite(rawRows) || rawRows <= 0 || start !== end)
  ) return null;
  if (
    eventType === 'output'
    && terminalTextUnitLength(sanitizeTerminalReplayText(delta.text)) !== end - start
  ) return null;
  return delta;
}

function settleTerminalFrameJob(job, value) {
  const waiters = job?.waiters || [];
  if (job) job.waiters = [];
  for (const resolve of waiters) {
    try { resolve(Boolean(value)); } catch(e) {}
  }
}

function terminalFrameAtLeast(left, right) {
  return Boolean(
    left
    && right
    && left.streamId === right.streamId
    && left.streamEpoch === right.streamEpoch
    && Number(left.frameSeq) >= Number(right.frameSeq)
  );
}

function terminalFrameEquals(left, right) {
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

function terminalDeltaEquals(left, right) {
  return Boolean(
    left
    && right
    && left.streamId === right.streamId
    && left.streamEpoch === right.streamEpoch
    && left.frameSeq === right.frameSeq
    && left.start === right.start
    && left.end === right.end
    && left.eventType === right.eventType
    && left.text === right.text
    && left.cols === right.cols
    && left.rows === right.rows
  );
}

function rememberTerminalDeltaSignature(view, delta) {
  if (!view || !delta) return;
  view.deltaSignatures.set(delta.frameSeq, delta);
  while (view.deltaSignatures.size > 4096) {
    view.deltaSignatures.delete(view.deltaSignatures.keys().next().value);
  }
}

function bufferTerminalDelta(view, delta) {
  if (!view || !delta) return;
  const existing = view.deltaBacklog.find(item => (
    item.streamId === delta.streamId && item.frameSeq === delta.frameSeq
  ));
  if (terminalDeltaEquals(existing, delta)) return;
  view.deltaBacklog.push(delta);
}

function pauseTerminalDeltaStream(view, reason = '') {
  if (!view) return;
  view.deltaPaused = true;
  if (reason) view.deltaRecoveryReason = reason;
  if (view.deltaQueue.length) {
    for (const delta of view.deltaQueue) bufferTerminalDelta(view, delta);
    view.deltaQueue = [];
  }
}

function terminalDeltaContainsAlternateBufferTransition(tail, text) {
  const value = String(tail || '') + String(text || '');
  const pattern = /\x1b\[\?([0-9;]*)([hl])/g;
  let match;
  while ((match = pattern.exec(value))) {
    if (match[1].split(';').map(Number).some(mode => mode === 47 || mode === 1047 || mode === 1049)) {
      return true;
    }
  }
  return false;
}

function advanceTerminalDeltaControlTail(view, text) {
  if (!view) return;
  view.deltaControlTail = (String(view.deltaControlTail || '') + String(text || '')).slice(-32);
}

function terminalDeltaRecoveryDelay(view) {
  const attempts = Math.max(0, Number(view?.deltaRecoveryAttempts || 0));
  return Math.min(5000, TERMINAL_FRAME_RECOVERY_RETRY_MS * Math.max(1, 2 ** Math.min(4, attempts)));
}

function scheduleTerminalScreenRefresh(view = activeTerminalView(), delay = 0) {
  if (
    !view?.frameMode
    || !view.deltaPaused
    || view.attaching
    || !isActiveTerminalView(view)
    || !termSocket?.connected
  ) return;
  if (view.frameFetchInFlight) {
    view.frameFetchDirty = true;
    return;
  }
  if (view.frameRefreshTimer) return;
  view.frameRefreshTimer = setTimeout(() => {
    view.frameRefreshTimer = 0;
    refreshTerminalScreenFrame(view);
  }, Math.max(0, Number(delay || 0)));
}

function queueTerminalHistoryResumeReconcile(view = activeTerminalView(), delay = 700) {
  if (!view || view.disposed) return false;
  view.historyResumeReconcilePending = true;
  if (view.historyResumeReconcileTimer) clearTimeout(view.historyResumeReconcileTimer);
  view.historyResumeReconcileTimer = setTimeout(() => {
    view.historyResumeReconcileTimer = 0;
    if (!view.historyResumeReconcilePending) return;
    if (!isActiveTerminalView(view) || !view.frameMode || view.attaching) {
      view.historyResumeReconcilePending = false;
      return;
    }
    view.historyResumeReconcilePending = false;
    beginTerminalDeltaRecovery(view, 'history resume settled');
  }, Math.max(0, Number(delay || 0)));
  return true;
}

function beginTerminalDeltaRecovery(view, reason, delta = null) {
  if (!view?.frameMode) return;
  if (delta) bufferTerminalDelta(view, delta);
  pauseTerminalDeltaStream(view, reason);
  scheduleTerminalScreenRefresh(view, 0);
}

function acceptTerminalDelta(view, delta) {
  if (!view?.term || !delta) return false;
  if (view.attaching || view.deltaPaused || view.frameWriting || view.pendingFrame) {
    bufferTerminalDelta(view, delta);
    return true;
  }
  if (delta.streamId !== view.frameStreamId) {
    if (delta.streamEpoch && view.frameStreamEpoch && delta.streamEpoch < view.frameStreamEpoch) return true;
    beginTerminalDeltaRecovery(view, 'stream changed', delta);
    return false;
  }
  if (delta.frameSeq <= Number(view.deltaAcceptedSeq ?? -1)) {
    const accepted = view.deltaSignatures.get(delta.frameSeq);
    if (accepted && !terminalDeltaEquals(accepted, delta)) {
      beginTerminalDeltaRecovery(view, 'conflicting duplicate delta', delta);
      return false;
    }
    return true;
  }
  if (
    delta.frameSeq !== Number(view.deltaAcceptedSeq ?? -1) + 1
    || delta.start !== Number(view.deltaAcceptedEnd || 0)
  ) {
    beginTerminalDeltaRecovery(view, 'ordered delta gap', delta);
    return false;
  }
  if (terminalDeltaContainsAlternateBufferTransition(view.deltaControlTail, delta.text)) {
    beginTerminalDeltaRecovery(view, 'alternate buffer transition', delta);
    return false;
  }

  view.deltaQueue.push(delta);
  view.deltaAcceptedSeq = delta.frameSeq;
  view.deltaAcceptedEnd = delta.end;
  view.historyStart = Math.min(Number(view.historyStart || delta.historyStart), delta.historyStart);
  view.historyEnd = Math.max(Number(view.historyEnd || 0), delta.end);
  rememberTerminalDeltaSignature(view, delta);
  advanceTerminalDeltaControlTail(view, delta.text);
  pumpTerminalRenderState(view);
  return true;
}

function reconcileTerminalDeltaBacklog(view) {
  if (!view) return false;
  const backlog = view.deltaBacklog;
  view.deltaBacklog = [];
  const relevant = [];
  for (const delta of backlog) {
    if (delta.streamId !== view.frameStreamId) {
      if (!delta.streamEpoch || delta.streamEpoch > Number(view.frameStreamEpoch || 0)) relevant.push(delta);
      continue;
    }
    if (delta.frameSeq > Number(view.deltaAcceptedSeq ?? -1)) {
      relevant.push(delta);
      continue;
    }
    const accepted = view.deltaSignatures.get(delta.frameSeq);
    if (accepted && !terminalDeltaEquals(accepted, delta)) relevant.push(delta);
  }
  relevant.sort((left, right) => left.frameSeq - right.frameSeq);
  view.deltaControlTail = '';

  for (let index = 0; index < relevant.length; index += 1) {
    const delta = relevant[index];
    if (delta.streamId !== view.frameStreamId) {
      for (const item of relevant.slice(index)) bufferTerminalDelta(view, item);
      beginTerminalDeltaRecovery(view, 'new stream while applying snapshot');
      return false;
    }
    if (delta.frameSeq <= Number(view.deltaAcceptedSeq ?? -1)) {
      const accepted = view.deltaSignatures.get(delta.frameSeq);
      if (accepted && !terminalDeltaEquals(accepted, delta)) {
        for (const item of relevant.slice(index)) bufferTerminalDelta(view, item);
        beginTerminalDeltaRecovery(view, 'conflicting snapshot backlog delta');
        return false;
      }
      continue;
    }
    if (
      delta.frameSeq !== Number(view.deltaAcceptedSeq ?? -1) + 1
      || delta.start !== Number(view.deltaAcceptedEnd || 0)
      || terminalDeltaContainsAlternateBufferTransition(view.deltaControlTail, delta.text)
    ) {
      for (const item of relevant.slice(index)) bufferTerminalDelta(view, item);
      beginTerminalDeltaRecovery(view, 'snapshot backlog gap');
      return false;
    }
    view.deltaQueue.push(delta);
    view.deltaAcceptedSeq = delta.frameSeq;
    view.deltaAcceptedEnd = delta.end;
    view.historyStart = Math.min(Number(view.historyStart || delta.historyStart), delta.historyStart);
    view.historyEnd = Math.max(Number(view.historyEnd || 0), delta.end);
    rememberTerminalDeltaSignature(view, delta);
    advanceTerminalDeltaControlTail(view, delta.text);
  }

  view.deltaPaused = false;
  view.deltaRecoveryReason = '';
  view.deltaRecoveryAttempts = 0;
  view.frameFetchDirty = false;
  pumpTerminalRenderState(view);
  return true;
}

function queueTerminalScreenFrame(view, data, options = {}) {
  const frame = normalizeTerminalScreenFrame(data);
  if (!view?.term || !frame || !frame.deltaSafe) return Promise.resolve(false);
  if (options.attachSeq !== undefined && !terminalAttachIsCurrent(view, options.attachSeq)) {
    return Promise.resolve(false);
  }

  const authoritative = options.authoritative === true;
  if (view.frameStreamId && view.frameStreamId !== frame.streamId) {
    if (!authoritative) return Promise.resolve(false);
    view.frameGeneration += 1;
    settleTerminalFrameJob(view.pendingFrame, false);
    view.pendingFrame = null;
    view.deltaQueue = view.deltaQueue.filter(delta => (
      delta.streamId === frame.streamId && delta.frameSeq > frame.frameSeq
    ));
    view.deltaBacklog = view.deltaBacklog.filter(delta => (
      delta.streamId === frame.streamId && delta.frameSeq > frame.frameSeq
    ));
    for (const [sequence, delta] of view.deltaSignatures) {
      if (delta.streamId !== frame.streamId || delta.frameSeq <= frame.frameSeq) {
        view.deltaSignatures.delete(sequence);
      }
    }
    view.deltaControlTail = '';
    view.frameStreamId = frame.streamId;
    view.frameStreamEpoch = frame.streamEpoch;
    view.frameAppliedSeq = -1;
    view.frameAppliedEnd = 0;
  } else if (!view.frameStreamId) {
    view.frameStreamId = frame.streamId;
    view.frameStreamEpoch = frame.streamEpoch;
  } else {
    view.frameStreamEpoch = Math.max(Number(view.frameStreamEpoch || 0), frame.streamEpoch);
  }
  view.frameMode = true;
  pauseTerminalDeltaStream(view, options.reason || 'authoritative frame');

  if (
    frame.streamId === view.frameStreamId
    && frame.frameSeq < Number(view.frameAppliedSeq ?? -1)
  ) {
    scheduleTerminalScreenRefresh(view, terminalDeltaRecoveryDelay(view));
    return Promise.resolve(false);
  }

  return new Promise(resolve => {
    if (
      view.frameWriting
      && view.frameWriting.generation === view.frameGeneration
      && terminalFrameAtLeast(view.frameWriting.frame, frame)
    ) {
      if (terminalFrameEquals(view.frameWriting.frame, frame)) {
        view.frameWriting.waiters.push(resolve);
        return;
      }
      if (view.frameWriting.frame.frameSeq > frame.frameSeq) {
        resolve(true);
        return;
      }
    }
    if (view.pendingFrame && terminalFrameAtLeast(view.pendingFrame.frame, frame)) {
      if (terminalFrameEquals(view.pendingFrame.frame, frame)) {
        view.pendingFrame.waiters.push(resolve);
        return;
      }
      if (view.pendingFrame.frame.frameSeq > frame.frameSeq) {
        resolve(true);
        return;
      }
      settleTerminalFrameJob(view.pendingFrame, false);
      view.pendingFrame = null;
    }

    const waiters = view.pendingFrame?.waiters || [];
    view.pendingFrame = {
      frame,
      generation: view.frameGeneration,
      waiters: waiters.concat(resolve),
    };
    pumpTerminalRenderState(view);
  });
}

function commitTerminalFrame(view, frame) {
  view.frameAppliedSeq = frame.frameSeq;
  view.frameAppliedEnd = frame.frameEnd;
  view.deltaAcceptedSeq = frame.frameSeq;
  view.deltaAcceptedEnd = frame.frameEnd;
  view.deltaSignatures.clear();
  view.renderedEnd = frame.frameEnd;
  view.replayStart = frame.frameEnd;
  view.replayEnd = frame.frameEnd;
  view.replayUnits = 0;
  view.replayBuffer = '';
  view.historyStart = frame.historyStart;
  view.historyEnd = Math.max(frame.historyEnd, frame.frameEnd);
  view.historyExhausted = true;
  view.bufferVersion = [frame.streamId, frame.frameSeq, frame.frameEnd, 'frame'].join(':');
  view.hydrated = true;
}

function pumpTerminalScreenFrame(view) {
  pumpTerminalRenderState(view);
}

function pumpTerminalRenderState(view) {
  if (!view?.term || view.frameWriting || view.deltaWriting) return;
  if (view.pendingFrame) {
    const job = view.pendingFrame;
    view.pendingFrame = null;
    view.frameWriting = job;
    view.replaying = true;
    view.hydrated = false;
    view.replaySeq += 1;
    view.pendingOutput = '';
    resetTerminalOutputHighlightState(view);
    preserveTerminalFrameSurface(view);
    try { view.term.resize(job.frame.cols, job.frame.rows); } catch(e) {}
    try { view.term.reset(); } catch(e) {
      try { view.term.clear(); } catch(_) {}
    }

    const finishFrame = success => {
      const current = Boolean(
        success
        && view.frameWriting === job
        && view.frameGeneration === job.generation
        && view.frameStreamId === job.frame.streamId
      );
      if (current) commitTerminalFrame(view, job.frame);
      view.frameWriting = null;
      settleTerminalFrameJob(job, current);
      if (view.pendingFrame) {
        view.replaying = true;
        pumpTerminalRenderState(view);
        return;
      }
      fitTerminalViewNow(view);
      refreshTerminalViewSurface(view);
      view.replaying = false;
      if (current) {
        const reconciled = reconcileTerminalDeltaBacklog(view);
        if (reconciled) {
          flushPendingTerminalInput(view);
          if (!view.attaching) releaseTerminalFrameSurface(view);
        }
        if (terminalShouldFollowOutput(view)) scrollTerminalToBottom(20, view, { force: false });
        scheduleTerminalPromptNavigation(view, { scan: true, force: true });
      } else if (view.frameMode && isActiveTerminalView(view)) {
        beginTerminalDeltaRecovery(view, 'snapshot write was superseded');
      }
      pumpTerminalRenderState(view);
    };

    const displaySnapshot = formatTerminalSnapshotForDisplay(view, job.frame.snapshot);
    try {
      view.term.write(displaySnapshot, () => finishFrame(true));
    } catch(e) {
      finishFrame(false);
    }
    return;
  }
  if (view.deltaPaused || !view.deltaQueue.length) return;

  const delta = view.deltaQueue.shift();
  const job = { delta, generation: view.frameGeneration };
  view.deltaWriting = job;
  const followOutput = terminalShouldFollowOutput(view);
  const finishDelta = success => {
    const current = Boolean(
      success
      && view.deltaWriting === job
      && view.frameGeneration === job.generation
      && view.frameStreamId === delta.streamId
    );
    if (current) {
      view.frameAppliedSeq = delta.frameSeq;
      view.frameAppliedEnd = delta.end;
      view.renderedEnd = delta.end;
      view.bufferVersion = [delta.streamId, delta.frameSeq, delta.end, 'delta'].join(':');
      if (followOutput) scrollTerminalToBottom(20, view, { force: false });
      if (delta.eventType === 'resize' && isActiveTerminalView(view)) {
        scheduleTerminalLayoutSync({ claimGeometry: terminalCanClaimGeometry() });
      }
      if (delta.text) consumeTerminalPromptSubmissionScan(view);
    }
    view.deltaWriting = null;
    if (!current && !view.pendingFrame && view.frameMode && isActiveTerminalView(view)) {
      beginTerminalDeltaRecovery(view, 'delta write was superseded');
    }
    pumpTerminalRenderState(view);
  };

  if (delta.eventType === 'resize') {
    try {
      view.term.resize(delta.cols, delta.rows);
      Promise.resolve().then(() => finishDelta(true));
    } catch(e) {
      finishDelta(false);
    }
    return;
  }
  if (!delta.text) {
    Promise.resolve().then(() => finishDelta(true));
    return;
  }
  const displayText = formatTerminalOutputForDisplay(view, delta.text);
  try {
    view.term.write(displayText, () => finishDelta(true));
  } catch(e) {
    finishDelta(false);
  }
}

async function refreshTerminalScreenFrame(view = activeTerminalView()) {
  if (!view?.frameMode || !view.deltaPaused || !isActiveTerminalView(view)) return false;
  if (view.frameFetchInFlight) {
    view.frameFetchDirty = true;
    return view.frameFetchInFlight;
  }
  view.frameFetchDirty = false;
  view.deltaRecoveryAttempts += 1;
  const generation = view.frameGeneration;
  const abortController = new AbortController();
  view.frameFetchAbort = abortController;
  const params = {
    workspace_id: view.workspaceId,
    terminal_id: view.terminalId,
  };
  let applied = false;
  const request = (async () => {
    try {
      const data = await backendApi.terminals.screen(params, {
        timeout: 8000,
        signal: abortController.signal,
      });
      if (!isActiveTerminalView(view) || view.frameGeneration !== generation) return false;
      return await queueTerminalScreenFrame(view, data, {
        authoritative: true,
        reason: 'gap recovery',
      });
    } catch(e) {
      return false;
    }
  })();
  view.frameFetchInFlight = request;
  try {
    applied = await request;
    return applied;
  } finally {
    if (view.frameFetchInFlight === request) view.frameFetchInFlight = null;
    if (view.frameFetchAbort === abortController) view.frameFetchAbort = null;
    const retry = view.deltaPaused && isActiveTerminalView(view) && (view.frameFetchDirty || !applied);
    view.frameFetchDirty = false;
    if (retry) scheduleTerminalScreenRefresh(view, terminalDeltaRecoveryDelay(view));
  }
}

function appendTerminalReplayWindow(view, text, metadata = {}) {
  if (!view) return;
  const value = sanitizeTerminalReplayText(text);
  if (!value) return;
  const valueUnits = terminalTextUnitLength(value);
  const replayEnd = Number(view.replayEnd || 0);
  const outputStart = Number(metadata.start);
  const outputEnd = Number(metadata.end);
  const hasServerOffsets = Number.isFinite(outputStart) && Number.isFinite(outputEnd);

  let combined = String(view.replayBuffer || '');
  let combinedUnits = Number(view.replayUnits || terminalTextUnitLength(combined));
  if (hasServerOffsets && outputStart !== replayEnd) {
    combined = '';
    combinedUnits = 0;
  }
  combined += value;
  combinedUnits += valueUnits;
  const droppedUnits = Math.max(0, combinedUnits - TERMINAL_CLIENT_REPLAY_WINDOW_CHARS);
  if (droppedUnits) combined = combined.slice(terminalTextSliceIndex(combined, droppedUnits));

  view.replayBuffer = combined;
  view.replayUnits = combinedUnits - droppedUnits;
  view.replayEnd = hasServerOffsets ? outputEnd : replayEnd + valueUnits;
  view.replayStart = view.replayEnd - view.replayUnits;
  if (Number.isFinite(Number(metadata.historyStart))) {
    view.historyStart = Number(metadata.historyStart);
  }
  view.historyEnd = Math.max(Number(view.historyEnd || 0), view.replayEnd);
}

function estimateTerminalLineCount(text) {
  const value = String(text || '');
  if (!value) return 0;
  return value.split(/\r\n|\r|\n/).length;
}

function terminalIsAtBottom(view) {
  const active = view?.term?.buffer?.active;
  if (!active) return true;
  return (active.baseY - active.viewportY) <= TERMINAL_BOTTOM_FOLLOW_THRESHOLD;
}

function setTerminalFollowOutput(view, value) {
  if (!view) return;
  view.followOutput = Boolean(value);
}

function terminalShouldFollowOutput(view) {
  return !view || view.followOutput !== false;
}

function terminalAttachIsCurrent(view, attachSeq = activeAttachSeq) {
  return Boolean(view && Number(attachSeq) === activeAttachSeq && isActiveTerminalView(view));
}

function markTerminalWheelIntent(view, event) {
  if (!view || !isActiveTerminalView(view)) return;
  if (Number(event?.deltaY || 0) < 0) {
    setTerminalFollowOutput(view, false);
  }
}

function handleTerminalScroll(view) {
  if (!view || !isActiveTerminalView(view)) return;
  setTerminalFollowOutput(view, terminalIsAtBottom(view));
  scheduleTerminalPromptNavigation(view, { scan: false });
  maybeLoadTerminalHistory(view);
}

function stripTerminalControlsForPrompt(value) {
  return String(value || '')
    .replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, '')
    .replace(/\x1b(?:\[[0-?]*[ -/]*[@-~]|[ -/]*[0-~])/g, '')
    .replace(/[\x00-\x1f\x7f]/g, '');
}

function isTerminalUserInputLine(segment) {
  const visible = stripTerminalControlsForPrompt(segment);
  return visible.startsWith('› ');
}

function isTerminalUserInputContinuationLine(segment) {
  const visible = stripTerminalControlsForPrompt(segment);
  return visible.startsWith('  ') && Boolean(visible.trim());
}

function terminalLeadingControlEnd(segment) {
  const match = String(segment || '').match(
    /^(?:(?:\x1b\][^\x07\x1b]*(?:\x07|\x1b\\))|(?:\x1b(?:\[[0-?]*[ -/]*[@-~]|[ -/]*[0-~]))|[\x00-\x1f\x7f])*/
  );
  return match ? match[0].length : 0;
}

function keepUserInputBackgroundAfterSgr(segment) {
  return String(segment || '').replace(/\x1b\[[0-?]*[ -/]*m/g, match => match + TERMINAL_USER_INPUT_BG);
}

function resetTerminalOutputHighlightState(view) {
  if (!view) return;
  view.outputAtLineStart = true;
  view.outputUserInputHighlightOpen = false;
  view.outputUserInputContinuation = false;
}

function formatTerminalOutputForDisplay(view, text) {
  const value = String(text || '');
  if (!view || !value) return value;
  let result = '';
  let index = 0;

  while (index < value.length) {
    const newlineIndex = value.indexOf('\n', index);
    const segmentEnd = newlineIndex >= 0 ? newlineIndex : value.length;
    let segment = value.slice(index, segmentEnd);
    let carriageReturn = '';
    if (newlineIndex >= 0 && segment.endsWith('\r')) {
      segment = segment.slice(0, -1);
      carriageReturn = '\r';
    }

    if (view.outputAtLineStart) {
      const userInputLine = isTerminalUserInputLine(segment);
      const userInputContinuation = Boolean(
        view.outputUserInputContinuation
        && isTerminalUserInputContinuationLine(segment)
      );
      view.outputUserInputContinuation = false;
      if (userInputLine || userInputContinuation) {
        if (!view.outputUserInputHighlightOpen) {
          const prefixEnd = terminalLeadingControlEnd(segment);
          result += segment.slice(0, prefixEnd) + TERMINAL_USER_INPUT_BG;
          segment = segment.slice(prefixEnd);
        }
        view.outputUserInputHighlightOpen = true;
      }
      if (segment || newlineIndex >= 0) view.outputAtLineStart = false;
    }

    result += view.outputUserInputHighlightOpen
      ? keepUserInputBackgroundAfterSgr(segment)
      : segment;

    if (newlineIndex < 0) break;
    const continueUserInput = view.outputUserInputHighlightOpen;
    if (view.outputUserInputHighlightOpen) {
      result += TERMINAL_USER_INPUT_BG_END;
      view.outputUserInputHighlightOpen = false;
    }
    result += carriageReturn + '\n';
    view.outputAtLineStart = true;
    view.outputUserInputContinuation = continueUserInput;
    index = newlineIndex + 1;
  }

  return result;
}

function formatTerminalSnapshotForDisplay(view, text) {
  resetTerminalOutputHighlightState(view);
  let result = formatTerminalOutputForDisplay(view, text);
  if (view?.outputUserInputHighlightOpen) result += TERMINAL_USER_INPUT_BG_END;
  resetTerminalOutputHighlightState(view);
  return result;
}

function appendTerminalOutput(view, data, metadata = {}) {
  if (!view?.term) return;
  const text = String(data || '');
  if (!text) return;
  appendTerminalReplayWindow(view, text, metadata);
  const outputRenderedEnd = Number(view.replayEnd || 0);
  if (view.replaying) {
    view.pendingOutput = (view.pendingOutput + text).slice(-PENDING_TERMINAL_OUTPUT_LIMIT);
    return;
  }
  const followOutput = terminalShouldFollowOutput(view);
  const displayText = formatTerminalOutputForDisplay(view, text);
  try {
    view.term.write(displayText, () => {
      view.renderedEnd = outputRenderedEnd;
      queueTerminalSnapshot(view);
      consumeTerminalPromptSubmissionScan(view);
      if (followOutput) scrollTerminalToBottom(20, view, { force: false });
    });
  } catch(e) {}
}

function flushTerminalOutput(view, options = {}) {
  if (!view?.term || !view.pendingOutput) return;
  const text = view.pendingOutput;
  view.pendingOutput = '';
  const followOutput = options.scrollToBottom !== false && terminalShouldFollowOutput(view);
  const displayText = formatTerminalOutputForDisplay(view, text);
  const outputRenderedEnd = Number(view.replayEnd || 0);
  try {
    view.term.write(displayText, () => {
      view.renderedEnd = outputRenderedEnd;
      queueTerminalSnapshot(view);
      consumeTerminalPromptSubmissionScan(view);
      if (followOutput) scrollTerminalToBottom(20, view, { force: false });
    });
  } catch(e) {}
}

async function fetchTerminalHistoryRange(view, start, end) {
  if (!view || end <= start) return '';
  let cursor = Math.floor(Number(start || 0));
  const rangeEnd = Math.floor(Number(end || 0));
  let loaded = 0;
  let result = '';

  while (rangeEnd > cursor && loaded < TERMINAL_HISTORY_FETCH_MAX_CHARS) {
    const limit = Math.min(
      TERMINAL_HISTORY_PAGE_CHARS,
      rangeEnd - cursor,
      TERMINAL_HISTORY_FETCH_MAX_CHARS - loaded
    );
    const params = {
      workspace_id: view.workspaceId,
      terminal_id: view.terminalId,
      after: String(cursor),
      before: String(rangeEnd),
      limit: String(Math.max(4096, limit)),
    };
    const data = await backendApi.terminals.buffer(params);
    const page = String(data.buffer || '');
    const pageStart = Number(data.start ?? cursor);
    const pageEnd = Number(data.end ?? pageStart);
    if (
      !page
      || !Number.isFinite(pageStart)
      || !Number.isFinite(pageEnd)
      || pageStart !== cursor
      || pageEnd <= pageStart
    ) break;
    result += page;
    loaded += pageEnd - pageStart;
    cursor = pageEnd;
  }
  return cursor >= rangeEnd ? result : '';
}

async function syncHydratedTerminalView(view, buffer, bufferStart, bufferEnd, attachSeq) {
  if (!view?.term || !terminalAttachIsCurrent(view, attachSeq)) return;
  const previousEnd = Math.floor(Number(view.renderedEnd || view.replayEnd || 0));
  if (!Number.isFinite(previousEnd) || bufferEnd === previousEnd) {
    view.bufferVersion = [view.replayStart || 0, view.replayEnd || 0, 'preserved'].join(':');
    return;
  }

  const missedUnits = bufferEnd - previousEnd;
  let missedOutput = '';
  if (previousEnd >= bufferStart && missedUnits <= TERMINAL_HISTORY_FETCH_MAX_CHARS) {
    try {
      missedOutput = await fetchTerminalHistoryRange(view, previousEnd, bufferEnd);
    } catch(e) {
      missedOutput = '';
    }
  }

  if (!terminalAttachIsCurrent(view, attachSeq)) return;
  if (missedOutput) {
    appendTerminalOutput(view, missedOutput, {
      start: previousEnd,
      end: bufferEnd,
      historyStart: Math.min(Number(view.historyStart || bufferStart), bufferStart),
    });
  } else {
    setTerminalReplayWindow(view, buffer, bufferStart, bufferEnd);
    resetTerminalDisplay(view);
    await new Promise(resolve => {
      let resolved = false;
      const done = () => {
        if (resolved) return;
        resolved = true;
        resolve();
      };
      writeTerminalReplayBuffer(
        view.replayBuffer,
        done,
        attachSeq,
        view,
        [view.replayStart, view.replayEnd, view.replayBuffer.length, 'snapshot'].join(':')
      );
    });
  }
  view.bufferVersion = [view.replayStart || 0, view.replayEnd || 0, 'preserved'].join(':');
}

function connectTerminal() {
  if (!activeWorkspaceId) {
    termConnected = false;
    setTermStatus(false, '请先新增工作区');
    document.getElementById('termCwdLabel').textContent = '--';
    showTerminalEmptyState('请先新增工作区', '请先点击“新增工作区”，导入已有目录或新建目录。');
    return;
  }
  normalizeActiveTerminal();
  renderTerminalTabs();
  if (!activeTerminalId) {
    showNoTerminalState();
    return;
  }
  activateTerminalView();
  setTermStatus(false, '连接中...');
  if (termSocket && termSocket.connected) {
    attachTerminal(true);
    return;
  }

  termSocket = createTerminalSocket();

  termSocket.on(TERMINAL_SOCKET_EVENTS.connect, () => {
    attachTerminal(true);
  });

  termSocket.on(TERMINAL_SOCKET_EVENTS.ready, (data) => {
    if (data.attach_seq !== undefined && Number(data.attach_seq) !== activeAttachSeq) return;
    const workspaceId = data.workspace_id;
    const terminalId = data.terminal_id || data.terminal?.id || DEFAULT_TERMINAL;
    if (workspaceId && workspaceId !== activeWorkspaceId) return;
    if (terminalId && terminalId !== activeTerminalId) return;
    setActiveTerminal(terminalId);
    const view = activateTerminalView(workspaceId || activeWorkspaceId, terminalId);
    if (view) view.checkpointSupported = Boolean(data.checkpoint_supported);
    termConnected = true;
    if (data.workspace) mergeWorkspaceSummary(data.workspace);
    if (data.terminal) {
      upsertTerminalSummary(data.terminal);
    } else {
      upsertTerminalSummary({
        id: terminalId,
        name: terminalId,
        workspace_id: activeWorkspaceId,
        pid: data.pid,
        alive: true,
        usable: true,
        cwd: data.cwd,
      });
    }
    renderWorkspaceSwitch();
    renderTerminalTabs();
    setTermStatus(true, activeWorkspaceLabel() + ' / ' + terminalLabel() + ' 已连接');
    document.getElementById('termCwdLabel').textContent = data.cwd || '--';
    const attachSeq = Number(data.attach_seq ?? activeAttachSeq);
    const finishReady = () => {
      flushPendingTerminalInput(view);
      view.acceptingQueuedInput = false;
      releaseTerminalFrameSurface(view);
    };
    const readyFrame = normalizeTerminalScreenFrame(data);
    const readyStreamId = String(data.stream_id || '');
    const readyStreamEpoch = Math.max(0, Math.floor(Number(data.stream_epoch || 0)));
    if (view && data.screen_frame_supported && readyFrame?.deltaSafe) {
      view.attaching = false;
      view.attachDirty = false;
      view.historyLoadSeq += 1;
      view.historyLoading = false;
      view.historyStart = Number(data.history_start ?? readyFrame.historyStart);
      view.historyEnd = Number(data.history_end ?? readyFrame.historyEnd);
      view.historyExhausted = true;
      view.preReadyFrame = null;
      queueTerminalScreenFrame(view, data, {
        authoritative: true,
        attachSeq,
        reason: 'terminal attach',
      }).then(applied => {
        if (!applied && terminalAttachIsCurrent(view, attachSeq)) {
          beginTerminalDeltaRecovery(view, 'attach snapshot was stale');
        }
      }).finally(() => {
        if (!terminalAttachIsCurrent(view, attachSeq)) return;
        finishReady();
        scheduleTerminalLayoutSync({ forceEmit: true });
      });
      return;
    }
    if (view && data.screen_frame_supported) {
      view.attaching = false;
      view.attachDirty = false;
      view.preReadyFrame = null;
      view.frameMode = true;
      view.frameStreamId = readyStreamId;
      view.frameStreamEpoch = readyStreamEpoch;
      view.frameAppliedSeq = -1;
      view.frameAppliedEnd = 0;
      view.deltaAcceptedSeq = -1;
      view.deltaAcceptedEnd = 0;
      view.deltaSignatures.clear();
      view.deltaControlTail = '';
      view.hydrated = false;
      view.historyLoadSeq += 1;
      view.historyLoading = false;
      view.historyStart = Number(data.history_start ?? 0);
      view.historyEnd = Number(data.history_end ?? data.buffer_end ?? 0);
      view.historyExhausted = true;
      flushPendingTerminalInput(view);
      view.acceptingQueuedInput = false;
      beginTerminalDeltaRecovery(view, 'attach snapshot is not delta-safe');
      scheduleTerminalLayoutSync({ forceEmit: true });
      return;
    }
    const cachedStreamId = String(view?.frameStreamId || '');
    const cachedStreamEpoch = Math.max(0, Math.floor(Number(view?.frameStreamEpoch || 0)));
    const fallbackStreamMatches = readyStreamId
      ? readyStreamId === cachedStreamId
        && (!readyStreamEpoch || !cachedStreamEpoch || readyStreamEpoch === cachedStreamEpoch)
      : !cachedStreamId;
    if (view) {
      view.attaching = false;
      view.attachDirty = false;
      view.preReadyFrame = null;
      view.frameMode = false;
      view.deltaPaused = false;
      view.deltaQueue = [];
      view.deltaBacklog = [];
      view.frameStreamId = readyStreamId;
      view.frameStreamEpoch = readyStreamEpoch;
      view.frameAppliedSeq = -1;
      view.frameAppliedEnd = 0;
      view.deltaAcceptedSeq = -1;
      view.deltaAcceptedEnd = 0;
      view.deltaSignatures.clear();
      view.deltaControlTail = '';
    }
    if (data.buffer !== undefined) {
      const buffer = data.buffer || '';
      const fallbackEnd = Number(data.history_end ?? buffer.length);
      const bufferEnd = Number(data.buffer_end ?? fallbackEnd);
      const bufferStart = Number(data.buffer_start ?? Math.max(0, bufferEnd - buffer.length));
      const hasPreservedView = Boolean(
        view
        && fallbackStreamMatches
        && view.hydrated
        && Number(view.renderedEnd || 0) === Number(view.replayEnd || 0)
        && terminalHasVisibleText(view)
        && !terminalReplayLooksCollapsed(view, view.replayBuffer)
      );
      if (view) {
        view.historyLoadSeq += 1;
        view.historyLoading = false;
        view.historyStart = Number(data.history_start ?? Math.max(0, bufferStart));
        view.historyEnd = Number(data.history_end ?? bufferEnd);
        view.historyExhausted = !data.has_older_buffer || bufferStart <= view.historyStart;
      }
      const bufferVersion = [
        bufferStart,
        bufferEnd,
        buffer.length,
        data.terminal?.last_output_at || 0,
      ].join(':');
      if (hasPreservedView) {
        syncHydratedTerminalView(view, buffer, bufferStart, bufferEnd, attachSeq)
          .finally(() => {
            if (!terminalAttachIsCurrent(view, attachSeq)) return;
            finishReady();
            scheduleTerminalLayoutSync({
              forceEmit: true,
            });
          });
        return;
      } else if (view && data.screen_snapshot && data.screen_snapshot_delta_safe === true) {
        restoreTerminalCheckpoint(view, data, finishReady, attachSeq).then(restored => {
          if (restored || !terminalAttachIsCurrent(view, attachSeq)) return;
          setTerminalReplayWindow(view, buffer, bufferStart, bufferEnd);
          resetTerminalDisplay(view);
          writeTerminalReplayBuffer(view.replayBuffer, finishReady, attachSeq, view, bufferVersion);
        });
        return;
      } else if (view && view.bufferVersion === bufferVersion && terminalHasVisibleText(view)) {
        finishReady();
        scrollTerminalToBottom(90, view, { force: false });
      } else {
        if (view) setTerminalReplayWindow(view, buffer, bufferStart, bufferEnd);
        resetTerminalDisplay(view);
        writeTerminalReplayBuffer(view?.replayBuffer ?? buffer, finishReady, attachSeq, view, bufferVersion);
      }
    } else {
      finishReady();
    }
    scheduleTerminalLayoutSync({
      forceEmit: true,
    });
    scrollTerminalToBottom(90, view, { force: false });
  });

  termSocket.on(TERMINAL_SOCKET_EVENTS.output, (data) => {
    const workspaceId = data.workspace_id;
    const terminalId = data.terminal_id || DEFAULT_TERMINAL;
    if (workspaceId && workspaceId !== activeWorkspaceId) return;
    if (terminalId && terminalId !== activeTerminalId) return;
    const view = terminalViews.get(terminalViewKey(workspaceId || activeWorkspaceId, terminalId)) || activeTerminalView();
    if (view?.historyResumeReconcilePending) queueTerminalHistoryResumeReconcile(view);
    if (view?.frameMode) {
      const delta = normalizeTerminalDelta(data);
      if (!delta) {
        beginTerminalDeltaRecovery(view, 'invalid terminal delta');
        return;
      }
      acceptTerminalDelta(view, delta);
      return;
    }
    appendTerminalOutput(view, data.data, {
      start: data.output_start,
      end: data.output_end,
      historyStart: data.history_start,
    });
  });

  termSocket.on(TERMINAL_SOCKET_EVENTS.frame, (data) => {
    const workspaceId = data.workspace_id;
    const terminalId = data.terminal_id || DEFAULT_TERMINAL;
    if (workspaceId && workspaceId !== activeWorkspaceId) return;
    if (terminalId && terminalId !== activeTerminalId) return;
    const view = terminalViews.get(terminalViewKey(workspaceId || activeWorkspaceId, terminalId)) || activeTerminalView();
    if (!view) return;
    if (view.attaching) return;
    if (view.deltaPaused || !view.hydrated) {
      queueTerminalScreenFrame(view, data, {
        authoritative: true,
        reason: 'pushed recovery frame',
      });
    }
  });

  termSocket.on(TERMINAL_SOCKET_EVENTS.exit, (data) => {
    const workspaceId = data.workspace_id;
    const terminalId = data.terminal_id || DEFAULT_TERMINAL;
    if (workspaceId && workspaceId !== activeWorkspaceId) return;
    if (terminalId && terminalId !== activeTerminalId) return;
    const activeView = terminalViews.get(terminalViewKey(workspaceId || activeWorkspaceId, terminalId)) || activeTerminalView();
    if (data.stream_id && activeView?.frameStreamId && data.stream_id !== activeView.frameStreamId) return;
    upsertTerminalSummary({
      id: terminalId,
      name: terminalId,
      workspace_id: activeWorkspaceId,
      alive: false,
      usable: false,
    });
    renderWorkspaceSwitch();
    renderTerminalTabs();
    termConnected = false;
    clearPendingTerminalInput();
    releaseTerminalFrameSurface(activeView);
    setTermStatus(false, '已断开 - 进程退出');
  });

  termSocket.on(TERMINAL_SOCKET_EVENTS.error, (data) => {
    termConnected = false;
    const view = activeTerminalView();
    if (view) view.acceptingQueuedInput = false;
    releaseTerminalFrameSurface(view);
    setTermStatus(false, data.error || '错误');
    toast('终端错误: ' + (data.error || ''), 'error');
  });

  termSocket.on(TERMINAL_SOCKET_EVENTS.disconnect, () => {
    termConnected = false;
    const view = activeTerminalView();
    pauseTerminalDeltaStream(view, 'socket disconnected');
    if (view) view.acceptingQueuedInput = false;
    setTermStatus(false, '连接断开');
  });

  termSocket.on(TERMINAL_SOCKET_EVENTS.connectError, () => {
    const view = activeTerminalView();
    if (view) view.acceptingQueuedInput = false;
    setTermStatus(false, '连接失败');
  });
}

function emitTerminalInput(data) {
  const view = activeTerminalView();
  if (view?.replaying && isGeneratedTerminalResponsePacket(data)) return;
  data = normalizeTerminalInput(data);
  if (!data) return;
  if (view?.acceptingQueuedInput || view?.attaching || view?.replaying) {
    queueTerminalInput(data, view, { force: true });
    return;
  }
  if (termSocket?.connected && termConnected) {
    markTerminalPromptSubmissionScan(view, data);
    termSocket.emit(TERMINAL_SOCKET_EVENTS.input, { attach_seq: activeAttachSeq, data });
  } else {
    queueTerminalInput(data, view);
  }
}

function normalizeTerminalInput(data) {
  return String(data || '')
    .replace(/\x1b\[[0-9;]*R/g, '')
    .replace(/\x1b\[[0-9;?=>]*c/g, '')
    .replace(/\x1b\[[IO]/g, '')
    .replace(/\x1b\][0-9;?]*;[^\x07\x1b]*(?:\x07|\x1b\\)/g, '');
}

function isCjkTerminalPunctuation(value) {
  return CJK_TERMINAL_PUNCTUATION_RE.test(String(value || ''));
}

function clearTerminalHelperTextarea(target) {
  if (!target || target.tagName !== 'TEXTAREA') return;
  setTimeout(() => {
    try { target.value = ''; } catch(e) {}
  }, 0);
}

function terminalInputEventText(event) {
  return event && typeof event.data === 'string' ? event.data : '';
}

function installTerminalImeFallback(
  container = document.getElementById('terminalContainer'),
  inputHandler = emitTerminalInput,
) {
  const textarea = container?.querySelector('textarea.xterm-helper-textarea') || container?.querySelector('textarea');
  if (!textarea || textarea.dataset.terminalImeFallback === '1') return;
  textarea.dataset.terminalImeFallback = '1';
  const state = { composition: null, lastComposition: null };

  const emitCompositionText = (commit, text, source) => {
    if (commit.handled) return false;
    commit.handled = true;
    commit.text = text;
    inputHandler(text, source);
    return true;
  };

  textarea.addEventListener('keydown', event => {
    const text = String(event.key || '');
    if (
      event.isComposing
      || event.ctrlKey
      || event.altKey
      || event.metaKey
      || !isCjkTerminalPunctuation(text)
    ) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    inputHandler(text, 'ime-keydown');
    clearTerminalHelperTextarea(event.target);
  }, true);

  textarea.addEventListener('compositionstart', () => {
    state.composition = { handled: false, text: '' };
    state.lastComposition = null;
  }, true);

  textarea.addEventListener('beforeinput', event => {
    const text = terminalInputEventText(event);
    if (!isCjkTerminalPunctuation(text)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const compositionInput = Boolean(
      state.composition
      || /composition/i.test(String(event.inputType || ''))
    );
    if (compositionInput) {
      const commit = state.composition || state.lastComposition || { handled: false, text: '' };
      emitCompositionText(commit, text, 'ime-beforeinput');
      state.lastComposition = commit;
    } else {
      inputHandler(text, 'ime-beforeinput');
    }
    clearTerminalHelperTextarea(event.target);
  }, true);

  textarea.addEventListener('compositionend', event => {
    const text = terminalInputEventText(event);
    const commit = state.composition || { handled: false, text: '' };
    state.composition = null;
    state.lastComposition = commit;
    if (!isCjkTerminalPunctuation(text)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    emitCompositionText(commit, text, 'ime-compositionend');
    clearTerminalHelperTextarea(event.target);
  }, true);
}

function queueTerminalInput(data, view = activeTerminalView()) {
  if (!view) return false;
  view.pendingInput += String(data || '');
  return true;
}

function flushPendingTerminalInput(view = activeTerminalView()) {
  if (
    !view
    || !isActiveTerminalView(view)
    || !termSocket
    || !termSocket.connected
    || !termConnected
    || !view.pendingInput
  ) return false;
  const data = view.pendingInput;
  view.pendingInput = '';
  markTerminalPromptSubmissionScan(view, data);
  termSocket.emit(TERMINAL_SOCKET_EVENTS.input, { attach_seq: activeAttachSeq, data });
  return true;
}

function clearPendingTerminalInput(view = activeTerminalView()) {
  if (!view) return;
  view.pendingInput = '';
  view.acceptingQueuedInput = false;
  view.promptNavScanAfterWrite = false;
}

function terminalBufferVisibleText(view) {
  if (!view?.term?.buffer?.active) return '';
  const active = view.term.buffer.active;
  const lines = [];
  const start = Math.max(0, active.length - 60);
  for (let index = start; index < active.length; index += 1) {
    try {
      lines.push(active.getLine(index)?.translateToString(true) || '');
    } catch(e) {}
  }
  return lines.join('\n').trim();
}

function terminalHasVisibleText(view) {
  const text = terminalBufferVisibleText(view);
  if (!text) return false;
  const normalized = text.replace(/\s+/g, ' ').trim();
  return normalized !== TERMINAL_EMPTY_VIEW_TEXT;
}

function stripTerminalReplayText(text) {
  return String(text || '')
    .replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, '')
    .replace(/\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g, '')
    .replace(/\r/g, '\n')
    .split('\n')
    .map(line => line.trimEnd())
    .filter(line => line.trim())
    .join('\r\n')
    .slice(-TERMINAL_FALLBACK_TEXT_CHARS);
}

function writeTerminalFallbackText(view, buffer) {
  const text = stripTerminalReplayText(buffer);
  if (!view?.term || !text) return Promise.resolve(false);
  const pendingOutput = view.pendingOutput;
  const replayEnd = Number(view.replayEnd || 0);
  preserveTerminalFrameSurface(view);
  resetTerminalDisplay(view, { preserveReplay: true });
  view.pendingOutput = pendingOutput;
  view.replaying = true;
  view.hydrated = false;
  const displayText = formatTerminalSnapshotForDisplay(view, text + '\r\n');
  return new Promise(resolve => {
    try {
      view.term.write('\x1b[90m--- 已用文本模式恢复终端缓存 ---\x1b[0m\r\n' + displayText, () => {
        view.renderedEnd = replayEnd;
        resolve(true);
      });
    } catch(e) {
      resolve(false);
    }
  });
}

function terminalReplayLooksCollapsed(view, buffer) {
  const value = String(buffer || '');
  if (value.length < 8192) return false;
  const visible = terminalBufferVisibleText(view);
  const visibleLines = visible ? visible.split('\n').filter(line => line.trim()).length : 0;
  if (visibleLines > 3) return false;
  const fallback = stripTerminalReplayText(value);
  return fallback.length > Math.max(2000, visible.length * 8);
}

function writeTerminalChunks(view, buffer, attachSeq, replaySeq, options = {}) {
  const value = String(buffer || '');
  if (!value) return Promise.resolve(true);
  const isCurrent = () => (
    view?.replaySeq === replaySeq
    && Number(attachSeq) === activeAttachSeq
    && isActiveTerminalView(view)
  );
  return new Promise(resolve => {
    let offset = 0;
    const writeNext = () => {
      if (!isCurrent()) {
        resolve(false);
        return;
      }
      const chunk = value.slice(offset, offset + TERMINAL_REPLAY_CHUNK_CHARS);
      offset += chunk.length;
      const displayChunk = options.formatOutput === false
        ? chunk
        : formatTerminalOutputForDisplay(view, chunk);
      try {
        view.term.write(displayChunk, () => {
          if (!isCurrent()) {
            resolve(false);
          } else if (offset >= value.length) {
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

async function restoreTerminalCheckpoint(view, data, finishReady, attachSeq) {
  const snapshot = String(data.screen_snapshot || '');
  const snapshotEnd = Math.floor(Number(data.screen_snapshot_end));
  const bufferEnd = Math.floor(Number(data.buffer_end ?? data.history_end ?? 0));
  if (!view?.term || !snapshot || !Number.isFinite(snapshotEnd) || snapshotEnd > bufferEnd) {
    return false;
  }

  const targetCols = Math.max(2, Number(view.term.cols || 80));
  const targetRows = Math.max(1, Number(view.term.rows || 24));
  const snapshotCols = Math.max(2, Number(data.screen_snapshot_cols || targetCols));
  const snapshotRows = Math.max(1, Number(data.screen_snapshot_rows || targetRows));
  const buffer = String(data.buffer || '');
  const bufferStart = Number(data.buffer_start ?? Math.max(0, bufferEnd - terminalTextUnitLength(buffer)));
  setTerminalReplayWindow(view, buffer, bufferStart, bufferEnd);
  resetTerminalDisplay(view);
  try { view.term.resize(snapshotCols, snapshotRows); } catch(e) {}

  const replaySeq = ++view.replaySeq;
  view.replaying = true;
  view.hydrated = false;
  const deltaPromise = snapshotEnd < bufferEnd
    ? fetchTerminalHistoryRange(view, snapshotEnd, bufferEnd)
    : Promise.resolve('');

  const displaySnapshot = formatTerminalSnapshotForDisplay(view, snapshot);
  const snapshotWritten = await writeTerminalChunks(
    view,
    displaySnapshot,
    attachSeq,
    replaySeq,
    { formatOutput: false }
  );
  if (!snapshotWritten) return true;

  try { view.term.resize(targetCols, targetRows); } catch(e) {}
  let delta = '';
  try {
    delta = await deltaPromise;
  } catch(e) {
    delta = '';
  }
  if (snapshotEnd < bufferEnd && !delta) {
    if (view.replaySeq === replaySeq) {
      view.replaying = false;
      view.hydrated = false;
    }
    return false;
  }

  const deltaWritten = await writeTerminalChunks(
    view,
    delta,
    attachSeq,
    replaySeq
  );
  if (!deltaWritten) return true;
  if (view.replaySeq !== replaySeq || !terminalAttachIsCurrent(view, attachSeq)) return true;

  view.replaying = false;
  view.hydrated = true;
  view.renderedEnd = bufferEnd;
  view.bufferVersion = [snapshotEnd, bufferEnd, snapshot.length, 'checkpoint'].join(':');
  scheduleTerminalPromptNavigation(view, { scan: true, force: true });
  const hadPendingOutput = Boolean(view.pendingOutput);
  flushTerminalOutput(view, { scrollToBottom: true });
  if (!hadPendingOutput) queueTerminalSnapshot(view);
  scrollTerminalToBottom(40, view, { force: false });
  finishReady();
  scheduleTerminalLayoutSync({ forceEmit: true });
  return true;
}

function writeTerminalReplayBuffer(buffer, onDone, attachSeq = activeAttachSeq, view = activeTerminalView(), bufferVersion = '', options = {}) {
  if (!view) view = activateTerminalView();
  const shouldScrollToBottom = options.scrollToBottom !== false;
  if (!view?.term || !buffer) {
    if (view?.term && attachSeq === activeAttachSeq && isActiveTerminalView(view)) {
      view.replaying = false;
      view.hydrated = true;
      view.renderedEnd = Number(view.replayEnd || 0);
      view.bufferVersion = bufferVersion || 'empty';
    }
    if (shouldScrollToBottom) scrollTerminalToBottom(40, view, { force: false });
    if (view && attachSeq === activeAttachSeq && onDone) onDone();
    return;
  }
  const replaySeq = ++view.replaySeq;
  const replayTargetEnd = Number(view.replayEnd || 0);
  const isCurrentReplay = () => view.replaySeq === replaySeq && attachSeq === activeAttachSeq && isActiveTerminalView(view);
  view.replaying = true;
  view.hydrated = false;
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    if (!isCurrentReplay()) return;
    const finalize = async () => {
      const fallbackNeeded = !terminalHasVisibleText(view) || terminalReplayLooksCollapsed(view, buffer);
      const fallbackWritten = fallbackNeeded ? await writeTerminalFallbackText(view, buffer) : false;
      if (!isCurrentReplay()) return;
      if (fallbackNeeded && !fallbackWritten) {
        view.replaying = false;
        view.hydrated = false;
        setTimeout(() => {
          if (terminalAttachIsCurrent(view, attachSeq)) attachTerminal(true);
        }, 100);
        return;
      }
      view.replaying = false;
      view.hydrated = true;
      view.renderedEnd = replayTargetEnd;
      view.bufferVersion = bufferVersion || (buffer.length + ':');
      scheduleTerminalPromptNavigation(view, { scan: true, force: true });
      flushTerminalOutput(view, { scrollToBottom: shouldScrollToBottom });
      if (!fallbackNeeded) queueTerminalSnapshot(view);
      if (shouldScrollToBottom) scrollTerminalToBottom(40, view, { force: false });
      if (onDone) onDone();
    };
    finalize();
  };
  let offset = 0;
  const writeNext = () => {
    if (done) return;
    if (!isCurrentReplay()) {
      if (view.replaySeq === replaySeq) view.replaying = false;
      return;
    }
    const chunk = buffer.slice(offset, offset + TERMINAL_REPLAY_CHUNK_CHARS);
    offset += chunk.length;
    const displayChunk = formatTerminalOutputForDisplay(view, chunk);
    try {
      view.term.write(displayChunk, () => {
        if (done) return;
        if (!isCurrentReplay()) {
          if (view.replaySeq === replaySeq) view.replaying = false;
          return;
        }
        if (offset >= buffer.length) {
          finish();
        } else {
          setTimeout(writeNext, 0);
        }
      });
    } catch(e) {
      finish();
    }
  };
  writeNext();
}

function maybeLoadTerminalHistory(view) {
  if (!view?.term || view.frameMode || !view.hydrated || view.replaying || view.historyLoading || view.historyExhausted) return;
  if (!isActiveTerminalView(view)) return;
  const activeBuffer = view.term.buffer?.active;
  if (!activeBuffer || activeBuffer.viewportY > TERMINAL_HISTORY_SCROLL_THRESHOLD) return;
  loadOlderTerminalHistory(view);
}

async function loadOlderTerminalHistory(view) {
  if (!view?.term || view.historyLoading || view.historyExhausted) return;
  const before = Math.floor(Number(view.replayStart || 0));
  const historyStart = Math.floor(Number(view.historyStart || 0));
  if (!Number.isFinite(before) || before <= historyStart) {
    view.historyExhausted = true;
    return;
  }

  const replayUnits = Number(view.replayUnits || terminalTextUnitLength(view.replayBuffer || ''));
  const availableUnits = TERMINAL_CLIENT_HISTORY_WINDOW_CHARS - replayUnits;
  if (availableUnits < 4096) {
    view.historyExhausted = true;
    return;
  }

  view.historyLoading = true;
  const loadSeq = ++view.historyLoadSeq;
  try {
    const params = {
      workspace_id: view.workspaceId,
      terminal_id: view.terminalId,
      before: String(before),
      limit: String(Math.min(TERMINAL_HISTORY_PAGE_CHARS, availableUnits)),
    };
    const data = await backendApi.terminals.buffer(params);
    if (!isActiveTerminalView(view) || loadSeq !== view.historyLoadSeq) return;

    const page = String(data.buffer || '');
    const pageStart = Number(data.start ?? Math.max(0, before - page.length));
    const pageEnd = Number(data.end ?? before);
    view.historyStart = Number(data.history_start ?? view.historyStart ?? 0);
    view.historyEnd = Number(data.history_end ?? view.historyEnd ?? pageEnd);
    view.historyExhausted = !data.has_more || pageStart <= view.historyStart;
    if (!page || pageEnd <= pageStart) return;

    const addedLines = estimateTerminalLineCount(page);
    const combined = page + String(view.replayBuffer || '');
    if (terminalTextUnitLength(combined) > TERMINAL_CLIENT_HISTORY_WINDOW_CHARS) {
      view.historyExhausted = true;
      return;
    }
    setTerminalReplayWindowFromStart(view, combined, pageStart, TERMINAL_CLIENT_HISTORY_WINDOW_CHARS);
    const version = [view.replayStart, view.replayEnd, view.replayBuffer.length, 'history'].join(':');
    preserveTerminalFrameSurface(view);
    resetTerminalDisplay(view);
    writeTerminalReplayBuffer(view.replayBuffer, () => {
      try {
        const active = view.term.buffer?.active;
        const maxLine = Math.max(0, (active?.length || 0) - (view.term.rows || 0));
        view.term.scrollToLine(Math.min(maxLine, Math.max(0, addedLines - 1)));
      } catch(e) {}
      releaseTerminalFrameSurface(view);
    }, activeAttachSeq, view, version, { scrollToBottom: false });
  } catch(e) {
    if (isActiveTerminalView(view)) {
      try { console.warn('terminal history load failed', e); } catch(_) {}
    }
  } finally {
    if (loadSeq === view.historyLoadSeq) view.historyLoading = false;
  }
}

function scrollTerminalToBottom(delay = 40, view = activeTerminalView(), options = {}) {
  const target = view?.term || term;
  if (!target || typeof target.scrollToBottom !== 'function') return;
  const force = options.force !== false;
  const run = (shouldForce = force) => {
    if (!shouldForce && view?.followOutput === false) return;
    try { target.scrollToBottom(); } catch(e) {}
    if (view) setTerminalFollowOutput(view, true);
  };
  if (!view) {
    run(force);
    setTimeout(() => run(force), delay);
    return;
  }
  view.followScrollForce = view.followScrollForce || force;
  const runCoalesced = () => {
    const shouldForce = view.followScrollForce;
    view.followScrollForce = false;
    if (!shouldForce && view.followOutput === false) return;
    run(shouldForce);
  };
  if (!view.followScrollFrame) {
    if (typeof requestAnimationFrame === 'function') {
      view.followScrollFrame = requestAnimationFrame(() => {
        view.followScrollFrame = 0;
        runCoalesced();
      });
    } else {
      view.followScrollFrame = setTimeout(() => {
        view.followScrollFrame = 0;
        runCoalesced();
      }, 0);
    }
  }
  if (view.followScrollTimer) clearTimeout(view.followScrollTimer);
  view.followScrollTimer = setTimeout(() => {
    view.followScrollTimer = 0;
    runCoalesced();
  }, Math.max(0, Number(delay || 0)));
}

function resetTerminalDisplay(view = activeTerminalView(), options = {}) {
  const target = view?.term || term;
  if (!target) return;
  if (view) {
    if (!options.preserveReplay) view.replaySeq += 1;
    if (!options.preserveReplay) view.replaying = false;
    view.pendingOutput = '';
    view.renderedEnd = 0;
    view.promptNavScanAfterWrite = false;
    resetTerminalOutputHighlightState(view);
    resetTerminalPromptNavigation(view);
  }
  try {
    target.reset();
  } catch(e) {
    try { target.clear(); } catch(_) {}
  }
  try { target.clear(); } catch(e) {}
  try { target.write('\x1b[2J\x1b[3J\x1b[H'); } catch(e) {}
}

function isGeneratedTerminalResponsePacket(data) {
  return !normalizeTerminalInput(data);
}

function sendTerminalText(text) {
  emitTerminalInput(text);
}

function attachTerminal(clearBeforeAttach = false) {
  if (!activeWorkspaceId) {
    setTermStatus(false, '请先新增工作区');
    return;
  }
  if (!termSocket || !termSocket.connected) return;
  normalizeActiveTerminal();
  if (!activeTerminalId) {
    showNoTerminalState();
    return;
  }
  const view = activateTerminalView();
  if (!view) return;
  fitActiveTerminalView();
  preserveTerminalFrameSurface(view);
  const terminal = activeTerminalInfo();
  const attachSeq = ++terminalAttachSeq;
  activeAttachSeq = attachSeq;
  view.acceptingQueuedInput = true;
  if (view.frameRefreshTimer) clearTimeout(view.frameRefreshTimer);
  view.frameRefreshTimer = 0;
  try { view.frameFetchAbort?.abort(); } catch(e) {}
  view.frameFetchAbort = null;
  view.frameFetchInFlight = null;
  view.frameFetchDirty = false;
  view.frameGeneration += 1;
  settleTerminalFrameJob(view.pendingFrame, false);
  view.pendingFrame = null;
  view.deltaQueue = [];
  view.deltaBacklog = [];
  view.deltaSignatures.clear();
  view.deltaControlTail = '';
  view.deltaPaused = true;
  view.deltaRecoveryAttempts = 0;
  view.deltaRecoveryReason = 'terminal attach';
  view.frameMode = true;
  view.historyLoadSeq += 1;
  view.historyLoading = false;
  view.attaching = true;
  view.attachDirty = false;
  view.preReadyFrame = null;
  if (clearBeforeAttach) {
    setTermStatus(false, '连接 ' + activeWorkspaceLabel() + ' / ' + terminalLabel(terminal) + ' ...');
  }
  const claimGeometry = terminalCanClaimGeometry();
  if (claimGeometry) terminalLastGeometryClaimAt = Date.now();
  termSocket.emit(TERMINAL_SOCKET_EVENTS.attach, {
    attach_seq: attachSeq,
    workspace_id: activeWorkspaceId,
    terminal_id: activeTerminalId,
    replay_limit: TERMINAL_ATTACH_REPLAY_CHARS,
    cols: Math.max(2, view.term.cols || 80),
    rows: Math.max(1, view.term.rows || 24),
    claim_geometry: claimGeometry,
    sync_workspace_geometry: false,
  });
}

function setTermStatus(connected, text) {
  const dot = document.getElementById('termStatus');
  const label = document.getElementById('termStatusText');
  const chip = document.getElementById('termConnection');
  dot.className = 'terminal-status ' + (connected ? 'connected' : 'disconnected');
  if (chip) {
    chip.classList.toggle('connected', connected);
    chip.classList.toggle('disconnected', !connected);
  }
  label.textContent = text;
}

window.addEventListener('pagehide', () => {
  publishTerminalSnapshot(activeTerminalView(), { force: true });
});

// ===================== Codex Start =====================

const CODEX_CMD = 'codex --yolo';

function codexCommandForActiveWorkspace() {
  return String(activeWorkspaceInfo().agent_command || CODEX_CMD).trim() || CODEX_CMD;
}
