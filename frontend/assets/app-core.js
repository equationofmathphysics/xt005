let workspaceList = [];
const DEFAULT_WORKSPACE_ID = 'codexws';
let activeWorkspaceId = localStorage.getItem('active_workspace') || DEFAULT_WORKSPACE_ID;
const DEFAULT_TERMINAL = 'main';
let activeTerminalId = localStorage.getItem('active_terminal_' + activeWorkspaceId) || DEFAULT_TERMINAL;
let terminalFullscreen = false;
let pageFullscreen = false;
let bigScreenTimer = null;
let bigScreenTiles = new Map();
let bigScreenFocusedKey = '';
let statsTimer = null;
let statsRefreshInFlight = false;
let statsTerminalSignature = '';
let workspaceObservations = {};
let activeSideMode = 'history';
let codexShowArchived = localStorage.getItem('codex_history_show_archived') === '1';
let codexHistoryItems = [];
let codexHistoryById = new Map();
let codexHistoryRequestSeq = 0;
let workspaceCurrentDir = '';
let workspaceParentDir = null;
let workspaceCurrentFile = null;
let workspaceOriginalContent = '';
let workspaceModified = false;
let workspaceFileListRequestSeq = 0;
let workspaceFileReadRequestSeq = 0;
let workspaceMode = 'import';
let workspaceBrowserPath = '';
let workspaceBrowserParent = null;
let draggingWorkspaceId = '';
let suppressWorkspaceClick = false;
let workspaceSwitchSeq = 0;
let workspaceDragPointerId = null;
let workspaceDragStartX = 0;
let workspaceDragStartY = 0;
let workspaceDragActive = false;
const SIDEBAR_COLLAPSED_KEY = 'codex_sidebar_collapsed';
const CLOSE_ICON_SVG = '<svg viewBox="0 0 24 24" focusable="false" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
const BIG_SCREEN_REFRESH_MS = 10000;
const STATS_REFRESH_MS = 10000;
const BIG_SCREEN_LIMIT = 9;
const BIG_SCREEN_RECENT_OUTPUT_SECONDS = 180;
const TERMINAL_SCROLLBACK_LINES = 10000;
const BIG_SCREEN_SCROLLBACK_LINES = 1500;
const BIG_SCREEN_REPLAY_CHARS = 120000;
const CJK_TERMINAL_PUNCTUATION_RE = /^[\u3000-\u303f\uff01-\uff0f\uff1a-\uff20\uff3b-\uff40\uff5b-\uff65\u2014\u2018-\u201d\u2026\u00b7]+$/;
const WORKSPACE_BORDER_COLORS = [
  '#22c55e', '#3b82f6', '#f59e0b', '#e11d48', '#14b8a6',
  '#a855f7', '#f97316', '#06b6d4', '#84cc16', '#ec4899',
];
const TERMINAL_THEME = {
  background: '#0d1117',
  foreground: '#e6edf3',
  cursor: '#f2cc60',
  cursorAccent: '#0d1117',
  selectionBackground: '#264f78',
  selectionForeground: '#ffffff',
  black: '#161b22',
  red: '#ff7b72',
  green: '#7ee787',
  yellow: '#f2cc60',
  blue: '#79c0ff',
  magenta: '#d2a8ff',
  cyan: '#56d4dd',
  white: '#d0d7de',
  brightBlack: '#6e7681',
  brightRed: '#ffa198',
  brightGreen: '#aff5b4',
  brightYellow: '#f8e3a1',
  brightBlue: '#a5d6ff',
  brightMagenta: '#e2c5ff',
  brightCyan: '#b3f0ff',
  brightWhite: '#f0f6fc',
};
const BIG_SCREEN_TERMINAL_THEME = {
  ...TERMINAL_THEME,
  background: '#0b1018',
  foreground: '#dce6f2',
  cursor: '#8b949e',
  selectionBackground: '#1f3a5f',
};

function captureTerminalSurface(container, background = '#0d1117') {
  const rect = container?.getBoundingClientRect?.();
  if (!rect || rect.width < 1 || rect.height < 1) return null;
  const sourceTerminal = container.querySelector('.xterm');
  if (!sourceTerminal) return null;
  const sourceRect = sourceTerminal.getBoundingClientRect();
  const surface = sourceTerminal.cloneNode(true);
  surface.classList.add('terminal-frame-overlay-surface');
  surface.style.position = 'absolute';
  surface.style.left = (sourceRect.left - rect.left) + 'px';
  surface.style.top = (sourceRect.top - rect.top) + 'px';
  surface.style.width = sourceRect.width + 'px';
  surface.style.height = sourceRect.height + 'px';
  surface.querySelectorAll('textarea, input').forEach(element => element.remove());

  const sourceCanvases = Array.from(sourceTerminal.querySelectorAll('canvas'));
  const clonedCanvases = Array.from(surface.querySelectorAll('canvas'));
  let copiedCanvas = false;
  for (let index = 0; index < sourceCanvases.length; index += 1) {
    const source = sourceCanvases[index];
    const cloned = clonedCanvases[index];
    if (!cloned || !source.width || !source.height) continue;
    try {
      const context = cloned.getContext('2d');
      context?.drawImage(source, 0, 0);
      copiedCanvas = Boolean(context) || copiedCanvas;
    } catch(e) {}
  }
  const copiedDomText = Boolean(surface.querySelector('.xterm-rows')?.textContent);
  if (!copiedCanvas && !copiedDomText) return null;

  const overlay = document.createElement('div');
  overlay.className = 'terminal-frame-overlay';
  overlay.setAttribute('aria-hidden', 'true');
  overlay.style.background = background;
  overlay.appendChild(surface);
  return overlay;
}
