#!/usr/bin/env node

import { createRequire } from 'node:module';
import {
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
} from 'node:fs';
import { createServer } from 'node:http';
import { homedir, tmpdir } from 'node:os';
import { dirname, extname, join, normalize, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(TEST_DIR, '..');
const STRICT = process.env.TERMINAL_LAYOUT_REQUIRE_PLAYWRIGHT === '1';
const CHECKPOINT_DELTA = 'CHECKPOINT_DELTA';

function skip(message) {
  console.log(`SKIP: ${message}`);
  process.exitCode = STRICT ? 1 : 77;
}

function loadPlaywright() {
  const candidates = [];
  if (process.env.PLAYWRIGHT_NODE_MODULES) {
    candidates.push(resolve(process.env.PLAYWRIGHT_NODE_MODULES, 'playwright'));
  }
  candidates.push('playwright');

  const npxRoot = join(homedir(), '.npm', '_npx');
  if (existsSync(npxRoot)) {
    for (const entry of readdirSync(npxRoot).sort().reverse()) {
      candidates.push(join(npxRoot, entry, 'node_modules', 'playwright'));
    }
  }

  for (const candidate of candidates) {
    try {
      return require(candidate);
    } catch (_) {
      // Try the next local/cache location without downloading anything.
    }
  }
  return null;
}

function findChromiumExecutable() {
  const configured = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
  if (configured && existsSync(configured)) return configured;

  const cacheRoot = join(homedir(), '.cache', 'ms-playwright');
  if (existsSync(cacheRoot)) {
    const installs = readdirSync(cacheRoot)
      .filter(name => name.startsWith('chromium-'))
      .sort()
      .reverse();
    for (const install of installs) {
      for (const relative of [
        ['chrome-linux64', 'chrome'],
        ['chrome-linux', 'chrome'],
        ['chrome-linux64', 'headless_shell'],
        ['chrome-linux', 'headless_shell'],
      ]) {
        const candidate = join(cacheRoot, install, ...relative);
        if (existsSync(candidate)) return candidate;
      }
    }
  }

  for (const candidate of [
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
  ]) {
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

const MOCK_SOCKET_IO = String.raw`
(() => {
  const sockets = [];
  const events = [];
  const checkpoints = new Map();
  const screenResponses = [];
  window.__terminalLayoutSocketEvents = events;
  window.__terminalLayoutSockets = sockets;
  window.__terminalScreenResponses = screenResponses;
  const ESC = String.fromCharCode(27);
  const CRLF = String.fromCharCode(13, 10);
  window.__terminalSnapshot = text => (
    ESC + '[?25l' + ESC + '[0m' + ESC + '[2J' + ESC + '[3J' + ESC + '[H'
    + String(text || '') + ESC + '[?25h'
  );
  window.__queueTerminalScreenResponse = (payload, delay = 0, status = 200) => {
    screenResponses.push({
      payload,
      delay: Math.max(0, Number(delay || 0)),
      status: Math.max(100, Math.min(599, Number(status || 200))),
    });
  };

  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    if (url.pathname !== '/api/terminal-screen') return nativeFetch(input, init);
    const planned = screenResponses.shift();
    events.push({
      name: 'terminal_screen_request',
      workspace: url.searchParams.get('workspace_id') || '',
      terminal: url.searchParams.get('terminal_id') || '',
      planned: Boolean(planned),
      at: performance.now(),
    });
    if (!planned) {
      return new Response(JSON.stringify({ error: 'unexpected terminal screen request' }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    if (planned.delay) await new Promise(resolve => setTimeout(resolve, planned.delay));
    return new Response(JSON.stringify(planned.payload), {
      status: planned.status,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  class MockSocket {
    constructor() {
      this.connected = false;
      this.handlers = new Map();
      this.currentWorkspace = '';
      this.currentTerminal = '';
      sockets.push(this);
      setTimeout(() => {
        this.connected = true;
        this.dispatch('connect');
      }, 0);
    }

    on(name, callback) {
      const callbacks = this.handlers.get(name) || [];
      callbacks.push(callback);
      this.handlers.set(name, callbacks);
      return this;
    }

    dispatch(name, payload) {
      for (const callback of this.handlers.get(name) || []) callback(payload);
    }

    emit(name, payload = {}, acknowledge) {
      if (name === 'terminal_attach') {
        this.currentWorkspace = payload.workspace_id || '';
        this.currentTerminal = payload.terminal_id || 'a';
      }
      if (
        name === 'terminal_attach'
        || name === 'terminal_resize'
        || name === 'terminal_resize_workspace'
      ) {
        events.push({
          name,
          workspace: this.currentWorkspace,
          terminal: this.currentTerminal,
          cols: Number(payload.cols),
          rows: Number(payload.rows),
          viewOnly: name === 'terminal_attach' ? Boolean(payload.view_only) : null,
          attachSeq: payload.attach_seq === undefined ? null : Number(payload.attach_seq),
          at: performance.now(),
        });
      }
      if (name === 'terminal_input') {
        events.push({
          name,
          workspace: this.currentWorkspace,
          terminal: this.currentTerminal,
          attachSeq: payload.attach_seq === undefined ? null : Number(payload.attach_seq),
          data: String(payload.data || ''),
          at: performance.now(),
        });
      }
      if (name === 'terminal_snapshot') {
        const key = this.currentWorkspace + ':' + this.currentTerminal;
        checkpoints.set(key, {
          attachSeq: payload.attach_seq === undefined ? null : Number(payload.attach_seq),
          snapshot: String(payload.snapshot || ''),
          end: Number(payload.snapshot_end || 0),
          cols: Number(payload.cols || 80),
          rows: Number(payload.rows || 24),
        });
        setTimeout(() => acknowledge?.({ ok: true, snapshot_end: Number(payload.snapshot_end || 0) }), 0);
      }
      if (name === 'terminal_attach') {
        const terminal = this.currentTerminal;
        const workspace = this.currentWorkspace;
        if (window.__terminalDeltaAttachScenario && terminal === 'a') {
          window.__terminalDeltaAttachScenario = false;
          const queuedBeforeReady = CRLF + 'ATTACH_BEFORE_READY：中文连续';
          const queuedDuringSnapshot = CRLF + 'ATTACH_DURING_SNAPSHOT：中文连续';
          const readyEnd = 100;
          setTimeout(() => this.dispatch('terminal_output', {
            workspace_id: workspace,
            terminal_id: terminal,
            stream_id: 'delta-stream-a',
            stream_epoch: 1000,
            frame_seq: 101,
            output_start: readyEnd,
            output_end: readyEnd + [...queuedBeforeReady].length,
            history_start: 0,
            data: queuedBeforeReady,
          }), 0);
          setTimeout(() => this.dispatch('terminal_ready', {
            attach_seq: payload.attach_seq,
            workspace_id: workspace,
            terminal_id: terminal,
            cwd: '/tmp/codexws-terminal-layout-test',
            buffer: '',
            buffer_start: 0,
            buffer_end: readyEnd,
            history_start: 0,
            history_end: readyEnd,
            checkpoint_supported: true,
            screen_frame_supported: true,
            screen_snapshot_delta_safe: true,
            stream_id: 'delta-stream-a',
            stream_epoch: 1000,
            frame_seq: 100,
            frame_end: readyEnd,
            screen_snapshot: window.__terminalSnapshot('ATTACH_BASE：参考：中文连续'),
            screen_snapshot_end: readyEnd,
            screen_snapshot_cols: 120,
            screen_snapshot_rows: 35,
            terminal: {
              id: terminal,
              name: terminal.toUpperCase(),
              workspace_id: workspace,
              alive: true,
              usable: true,
              cwd: '/tmp/codexws-terminal-layout-test',
            },
          }), 20);
          setTimeout(() => this.dispatch('terminal_output', {
            workspace_id: workspace,
            terminal_id: terminal,
            stream_id: 'delta-stream-a',
            stream_epoch: 1000,
            frame_seq: 102,
            output_start: readyEnd + [...queuedBeforeReady].length,
            output_end: readyEnd + [...queuedBeforeReady].length + [...queuedDuringSnapshot].length,
            history_start: 0,
            data: queuedDuringSnapshot,
          }), 30);
          return this;
        }
        if (window.__terminalReattachScenario && terminal === 'a') {
          const plan = window.__terminalReattachScenario;
          window.__terminalReattachScenario = null;
          setTimeout(() => this.dispatch('terminal_ready', {
            attach_seq: payload.attach_seq,
            workspace_id: workspace,
            terminal_id: terminal,
            cwd: '/tmp/codexws-terminal-layout-test',
            buffer: '',
            buffer_start: 0,
            buffer_end: Number(plan.end),
            history_start: 0,
            history_end: Number(plan.end),
            checkpoint_supported: true,
            screen_frame_supported: true,
            screen_snapshot_delta_safe: true,
            stream_id: 'delta-stream-a',
            stream_epoch: 1000,
            frame_seq: Number(plan.seq),
            frame_end: Number(plan.end),
            screen_snapshot: window.__terminalSnapshot(plan.text),
            screen_snapshot_end: Number(plan.end),
            screen_snapshot_cols: 120,
            screen_snapshot_rows: 35,
            terminal: {
              id: terminal,
              name: terminal.toUpperCase(),
              workspace_id: workspace,
              alive: true,
              usable: true,
              cwd: '/tmp/codexws-terminal-layout-test',
            },
          }), Math.max(0, Number(plan.delay || 0)));
          return this;
        }
        if (window.__terminalUnsafeSnapshotScenario && terminal === 'a') {
          window.__terminalUnsafeSnapshotScenario = false;
          const buffer = 'UNSAFE_FALLBACK_BUFFER：参考：中文连续显示';
          const bufferEnd = [...buffer].length;
          setTimeout(() => this.dispatch('terminal_ready', {
            attach_seq: payload.attach_seq,
            workspace_id: workspace,
            terminal_id: terminal,
            cwd: '/tmp/codexws-terminal-layout-test',
            buffer,
            buffer_start: 0,
            buffer_end: bufferEnd,
            history_start: 0,
            history_end: bufferEnd,
            has_older_buffer: false,
            checkpoint_supported: true,
            screen_frame_supported: true,
            screen_snapshot_delta_safe: false,
            stream_id: 'unsafe-stream-a',
            stream_epoch: 2000,
            frame_seq: 1,
            frame_end: bufferEnd,
            screen_snapshot: window.__terminalSnapshot('UNSAFE_SNAPSHOT_MUST_NOT_APPEAR'),
            screen_snapshot_end: bufferEnd,
            screen_snapshot_cols: 120,
            screen_snapshot_rows: 35,
            terminal: {
              id: terminal,
              name: terminal.toUpperCase(),
              workspace_id: workspace,
              alive: true,
              usable: true,
              cwd: '/tmp/codexws-terminal-layout-test',
            },
          }), 0);
          return this;
        }
        const checkpoint = checkpoints.get(workspace + ':' + terminal) || null;
        const delta = checkpoint
          ? 'CHECKPOINT_DELTA'.slice(Number(checkpoint.end || 0))
          : '';
        setTimeout(() => this.dispatch('terminal_ready', {
          attach_seq: payload.attach_seq,
          workspace_id: workspace,
          terminal_id: terminal,
          cwd: '/tmp/codexws-terminal-layout-test',
          buffer: delta,
          buffer_start: checkpoint?.end || 0,
          buffer_end: (checkpoint?.end || 0) + delta.length,
          history_start: 0,
          history_end: (checkpoint?.end || 0) + delta.length,
          has_older_buffer: false,
          checkpoint_supported: true,
          screen_snapshot_delta_safe: Boolean(checkpoint?.snapshot),
          screen_snapshot: checkpoint?.snapshot || '',
          screen_snapshot_end: checkpoint?.end || 0,
          screen_snapshot_cols: checkpoint?.cols || 0,
          screen_snapshot_rows: checkpoint?.rows || 0,
          terminal: {
            id: terminal,
            name: terminal.toUpperCase(),
            workspace_id: workspace,
            alive: true,
            usable: true,
            cwd: '/tmp/codexws-terminal-layout-test',
          },
        }), 0);
      }
      return this;
    }
  }

  window.io = () => new MockSocket();
})();
`;

function json(response, body, status = 200) {
  const content = Buffer.from(JSON.stringify(body));
  response.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': content.length,
    'Cache-Control': 'no-store',
  });
  response.end(content);
}

function stageFrontend(tempRoot) {
  const publicRoot = join(tempRoot, 'public');
  cpSync(join(REPO_ROOT, 'frontend'), publicRoot, { recursive: true });

  const dependencies = [
    ['xterm', '5.3.0', 'xterm.js'],
    ['xterm', '5.3.0', 'xterm.css'],
    ['xterm-addon-fit', '0.8.0', 'xterm-addon-fit.js'],
    ['xterm-addon-serialize', '0.11.0', 'xterm-addon-serialize.js'],
  ];
  for (const parts of dependencies) {
    const source = join(REPO_ROOT, '.cdnlocal', ...parts);
    const targetDir = join(publicRoot, '.cdnlocal', ...parts.slice(0, -1));
    mkdirSync(targetDir, { recursive: true });
    cpSync(source, join(targetDir, parts.at(-1)));
  }
  return publicRoot;
}

function startServer(publicRoot, workspaceRoot) {
  const terminals = ['a', 'b'].map(id => ({
    id,
    name: id.toUpperCase(),
    workspace_id: 'layout-test',
    cwd: workspaceRoot,
    alive: true,
    usable: true,
  }));
  const workspace = {
    id: 'layout-test',
    name: 'Layout Test',
    cwd: workspaceRoot,
    exists: true,
    terminals,
    terminal: terminals[0],
  };

  const server = createServer((request, response) => {
    const url = new URL(request.url || '/', 'http://127.0.0.1');
    if (url.pathname === '/api/workspaces') {
      json(response, { workspaces: [workspace] });
      return;
    }
    if (url.pathname === '/api/workspace-terminals') {
      json(response, { workspace, terminals });
      return;
    }
    if (url.pathname === '/api/workspace-terminals/rename' && request.method === 'POST') {
      let body = '';
      request.setEncoding('utf8');
      request.on('data', chunk => { body += chunk; });
      request.on('end', () => {
        let data = {};
        try { data = JSON.parse(body || '{}'); } catch (_) {}
        const terminal = terminals.find(item => item.id === data.terminal_id);
        const name = String(data.name || '').trim();
        if (!terminal || !name) {
          json(response, { error: terminal ? '终端名称不能为空' : '终端不存在' }, terminal ? 400 : 404);
          return;
        }
        terminal.name = name;
        json(response, { ok: true, workspace, terminal });
      });
      return;
    }
    if (url.pathname === '/api/codex-history') {
      json(response, { conversations: [] });
      return;
    }
    if (url.pathname === '/api/terminal-buffer') {
      const after = Math.max(0, Number(url.searchParams.get('after') || 0));
      const before = Math.max(after, Number(url.searchParams.get('before') || CHECKPOINT_DELTA.length));
      const page = CHECKPOINT_DELTA.slice(after, before);
      json(response, {
        ok: true,
        buffer: page,
        start: after,
        end: after + page.length,
        history_start: 0,
        history_end: CHECKPOINT_DELTA.length,
        has_more: false,
        has_more_after: after + page.length < before,
      });
      return;
    }
    if (url.pathname === '/api/terminal-screen') {
      const esc = String.fromCharCode(27);
      const snapshot = esc + '[?25l' + esc + '[0m' + esc + '[2J' + esc + '[3J'
        + esc + '[H自动校准帧：参考：中文连续显示' + esc + '[?25h';
      json(response, {
        ok: true,
        screen_frame_supported: true,
        screen_snapshot_delta_safe: true,
        workspace_id: url.searchParams.get('workspace_id') || 'layout-test',
        terminal_id: url.searchParams.get('terminal_id') || 'a',
        stream_id: 'frame-stream-a',
        frame_seq: 4,
        frame_end: 40,
        history_start: 0,
        history_end: 40,
        screen_snapshot: snapshot,
        screen_snapshot_end: 40,
        screen_snapshot_cols: 120,
        screen_snapshot_rows: 35,
      });
      return;
    }
    if (url.pathname === '/api/workspace-observations/read' && request.method === 'POST') {
      json(response, { ok: true, workspace_id: 'layout-test', terminal_id: null });
      return;
    }
    if (url.pathname === '/api/system-stats') {
      json(response, {
        cpu_percent: 0,
        memory: { percent: 0, used: 0, total: 0 },
        process: { rss: 0 },
        workspaces: [workspace],
      });
      return;
    }
    if (url.pathname === '/.cdnlocal/socket.io/4.7.2/socket.io.min.js') {
      const content = Buffer.from(MOCK_SOCKET_IO);
      response.writeHead(200, {
        'Content-Type': 'text/javascript; charset=utf-8',
        'Content-Length': content.length,
        'Cache-Control': 'no-store',
      });
      response.end(content);
      return;
    }
    if (url.pathname === '/favicon.ico') {
      response.writeHead(204).end();
      return;
    }

    let pathname;
    try {
      pathname = decodeURIComponent(url.pathname);
    } catch (_) {
      response.writeHead(400).end();
      return;
    }
    if (pathname === '/') pathname = '/index.html';
    const relative = normalize(pathname).replace(/^[/\\]+/, '');
    const filename = resolve(publicRoot, relative);
    if (filename !== publicRoot && !filename.startsWith(publicRoot + sep)) {
      response.writeHead(403).end();
      return;
    }
    if (!existsSync(filename) || !statSync(filename).isFile()) {
      response.writeHead(404).end();
      return;
    }
    const mime = {
      '.css': 'text/css; charset=utf-8',
      '.html': 'text/html; charset=utf-8',
      '.js': 'text/javascript; charset=utf-8',
    }[extname(filename)] || 'application/octet-stream';
    const content = readFileSync(filename);
    response.writeHead(200, {
      'Content-Type': mime,
      'Content-Length': content.length,
      'Cache-Control': 'no-store',
    });
    response.end(content);
  });

  return new Promise((resolvePromise, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      resolvePromise({
        server,
        url: `http://127.0.0.1:${address.port}/`,
      });
    });
  });
}

async function closeServer(server) {
  await new Promise(resolvePromise => server.close(resolvePromise));
}

function assert(condition, message, details = null) {
  if (condition) return;
  const suffix = details === null ? '' : `\n${JSON.stringify(details, null, 2)}`;
  throw new Error(`${message}${suffix}`);
}

async function snapshot(page, label, terminal, requiredEvent, eventStart = 0) {
  try {
    await page.waitForFunction(
      ({ expectedTerminal, start, eventName }) => {
        if (typeof activeTerminalView !== 'function') return false;
        const view = activeTerminalView();
        const proposed = view?.fit?.proposeDimensions?.();
        if (!view || view.terminalId !== expectedTerminal || !proposed || !termConnected) return false;
        if (view.term.cols !== proposed.cols || view.term.rows !== proposed.rows) return false;
        const events = (window.__terminalLayoutSocketEvents || []).slice(start);
        return events.some(event => (
          (event.name === eventName
            || (eventName === 'terminal_resize' && event.name === 'terminal_resize_workspace'))
          && event.terminal === expectedTerminal
          && event.cols === proposed.cols
          && event.rows === proposed.rows
        ));
      },
      { expectedTerminal: terminal, start: eventStart, eventName: requiredEvent },
      { timeout: 5000 },
    );
  } catch (error) {
    const diagnostic = await page.evaluate(start => {
      const view = typeof activeTerminalView === 'function' ? activeTerminalView() : null;
      const proposed = view?.fit?.proposeDimensions?.() || null;
      return {
        activeTerminalId: typeof activeTerminalId === 'undefined' ? null : activeTerminalId,
        termConnected: typeof termConnected === 'undefined' ? null : termConnected,
        viewTerminal: view?.terminalId || null,
        term: view ? { cols: view.term.cols, rows: view.term.rows } : null,
        proposed,
        events: (window.__terminalLayoutSocketEvents || []).slice(start),
      };
    }, eventStart);
    throw new Error(`${label}: timed out waiting for ${requiredEvent}\n${JSON.stringify(diagnostic, null, 2)}`, { cause: error });
  }

  const state = await page.evaluate(({ expectedTerminal, start }) => {
    const view = activeTerminalView();
    const proposed = view.fit.proposeDimensions();
    return {
      label: expectedTerminal,
      activeTerminalId,
      termConnected,
      viewTerminal: view.terminalId,
      term: { cols: view.term.cols, rows: view.term.rows },
      proposed: { cols: proposed.cols, rows: proposed.rows },
      mount: {
        width: Math.round(view.mount.getBoundingClientRect().width),
        height: Math.round(view.mount.getBoundingClientRect().height),
      },
      events: (window.__terminalLayoutSocketEvents || []).slice(start),
    };
  }, { expectedTerminal: terminal, start: eventStart });
  state.label = label;

  assert(state.activeTerminalId === terminal, `${label}: unexpected active terminal`, state);
  assert(state.termConnected, `${label}: terminal did not become ready`, state);
  assert(state.viewTerminal === terminal, `${label}: active view is stale`, state);
  assert(state.term.cols === state.proposed.cols, `${label}: xterm cols do not match FitAddon`, state);
  assert(state.term.rows === state.proposed.rows, `${label}: xterm rows do not match FitAddon`, state);
  return state;
}

async function eventCount(page) {
  return page.evaluate(() => (window.__terminalLayoutSocketEvents || []).length);
}

async function clickTerminal(page, terminal) {
  await page.locator(`.terminal-tab[data-terminal-id="${terminal}"]`).click();
}

async function run() {
  const playwright = loadPlaywright();
  if (!playwright?.chromium) {
    skip('Playwright was not found in node_modules or the existing npx cache');
    return;
  }
  const executablePath = findChromiumExecutable();
  if (!executablePath) {
    skip('no existing Chromium/Chrome executable was found');
    return;
  }

  const tempRoot = mkdtempSync(join(tmpdir(), 'codexws-layout-regression-'));
  const workspaceRoot = join(tempRoot, 'workspace');
  mkdirSync(workspaceRoot);
  const publicRoot = stageFrontend(tempRoot);
  const { server, url } = await startServer(publicRoot, workspaceRoot);
  let browser;
  const reports = [];
  const pageErrors = [];

  try {
    browser = await playwright.chromium.launch({
      executablePath,
      headless: true,
      args: ['--disable-dev-shm-usage'],
    });
    const page = await browser.newPage({ viewport: { width: 760, height: 760 } });
    page.on('pageerror', error => pageErrors.push(error?.stack || String(error)));
    page.on('console', message => {
      if (message.type() === 'error') pageErrors.push(`console: ${message.text()}`);
    });

    await page.goto(url, { waitUntil: 'networkidle' });
    await page.locator('.terminal-tab[data-terminal-id="a"]').waitFor();

    const statsDomStable = await page.evaluate(() => {
      const workspaceButton = document.querySelector(`.workspace-btn[data-workspace-id="${CSS.escape(activeWorkspaceId)}"]`);
      const terminalButton = document.querySelector(`.terminal-tab[data-terminal-id="${CSS.escape(activeTerminalId)}"]`);
      const grouped = {};
      for (const workspace of workspaceList) {
        grouped[workspace.id] = (workspace.terminals || []).map(terminal => ({ ...terminal }));
      }
      statsTerminalSignature = terminalStatsStructureSignature(grouped);
      const active = grouped[activeWorkspaceId]?.find(terminal => terminal.id === activeTerminalId);
      if (active) {
        active.alive = true;
        active.usable = true;
      }
      const runningEvent = {
        id: 'stats-dom-running',
        timestamp: Math.floor(Date.now() / 1000),
        workspace_id: activeWorkspaceId,
        terminal_id: activeTerminalId,
        status: 'running',
        source: 'codex-hook',
        session_id: '',
        turn_id: 'stats-dom-turn',
      };
      applySystemStats({
        cpu_percent: 1,
        memory: { used: 1, total: 2 },
        process: { rss: 1 },
        terminal_counts: { total: 2, active: 2 },
        terminals_by_workspace: grouped,
        observations_by_workspace: {
          [activeWorkspaceId]: {
            workspace_id: activeWorkspaceId,
            unread_hooks: 0,
            terminals: [{
              terminal_id: activeTerminalId,
              unread_hooks: 0,
              hook_events: [runningEvent],
            }],
            recent_hook_events: [runningEvent],
          },
        },
      });
      return {
        workspaceStable: workspaceButton === document.querySelector(`.workspace-btn[data-workspace-id="${CSS.escape(activeWorkspaceId)}"]`),
        terminalStable: terminalButton === document.querySelector(`.terminal-tab[data-terminal-id="${CSS.escape(activeTerminalId)}"]`),
        terminalState: terminalButton?.querySelector('.workspace-dot')?.className || '',
      };
    });
    assert(
      statsDomStable.workspaceStable
        && statsDomStable.terminalStable
        && statsDomStable.terminalState.includes('busy'),
      'system stats replaced navigation DOM instead of updating status in place',
      statsDomStable,
    );

    const codexHookLightAudit = await page.evaluate(async () => {
      const now = Math.floor(Date.now() / 1000);
      const grouped = {};
      for (const workspace of workspaceList) {
        grouped[workspace.id] = (workspace.terminals || []).map(terminal => ({
          ...terminal,
          alive: true,
          usable: true,
          codex_active: true,
          has_codex_process: true,
        }));
      }
      const hookEvent = (terminalId, status, offset) => ({
        id: `${terminalId}-${status}-${offset}`,
        timestamp: now + offset,
        workspace_id: 'layout-test',
        terminal_id: terminalId,
        status,
        source: 'codex-hook',
        session_id: '',
        turn_id: `turn-${terminalId}`,
      });
      const applyHooks = (aEvents, aUnread, bEvents, bUnread) => {
        applySystemStats({
          cpu_percent: 1,
          memory: { used: 1, total: 2 },
          process: { rss: 1 },
          terminal_counts: { total: 2, active: 2 },
          terminals_by_workspace: grouped,
          observations_by_workspace: {
            'layout-test': {
              workspace_id: 'layout-test',
              unread_hooks: aUnread + bUnread,
              terminals: [
                { terminal_id: 'a', unread_hooks: aUnread, hook_events: aEvents },
                { terminal_id: 'b', unread_hooks: bUnread, hook_events: bEvents },
              ],
              recent_hook_events: [...aEvents, ...bEvents],
            },
          },
        });
      };
      const terminalClass = terminalId => document.querySelector(
        `.terminal-tab[data-terminal-id="${terminalId}"] .workspace-dot`
      )?.className || '';

      const bRunning = hookEvent('b', 'running', 0);
      applyHooks([], 0, [bRunning], 0);
      const backgroundRunning = terminalClass('b');

      const bDone = hookEvent('b', 'done', 1);
      applyHooks([], 0, [bRunning, bDone], 1);
      const backgroundDone = terminalClass('b');
      const bigScreenDoneIsRunning = terminalHasRunningHook(grouped['layout-test'][1], now + 2);

      const aRunning = hookEvent('a', 'running', 2);
      const aDone = hookEvent('a', 'done', 3);
      applyHooks([aRunning, aDone], 1, [bRunning, bDone], 1);
      const currentDone = terminalClass('a');

      await markCodexCompletionSeen('layout-test', 'b');
      const backgroundRead = terminalClass('b');
      return {
        backgroundRunning,
        backgroundDone,
        bigScreenDoneIsRunning,
        currentDone,
        backgroundRead,
        statsRefreshMs: STATS_REFRESH_MS,
      };
    });
    assert(
      codexHookLightAudit.backgroundRunning.includes('busy')
        && codexHookLightAudit.backgroundDone.includes('attention')
        && !codexHookLightAudit.bigScreenDoneIsRunning
        && codexHookLightAudit.currentDone.includes('idle')
        && !codexHookLightAudit.currentDone.includes('attention')
        && codexHookLightAudit.backgroundRead.includes('idle')
        && codexHookLightAudit.statsRefreshMs === 10000,
      'Codex Hook status lights did not follow busy -> attention -> read semantics',
      codexHookLightAudit,
    );

    const bigScreenDeltaState = await page.evaluate(async () => {
      const writes = [];
      const resizes = [];
      let resets = 0;
      let failingWrite = '';
      const fakeTerm = {
        cols: 80,
        rows: 24,
        options: { fontSize: 11 },
        write(value, callback) {
          if (String(value || '') === failingWrite) {
            failingWrite = '';
            throw new Error('planned xterm write failure');
          }
          writes.push(String(value || ''));
          setTimeout(() => callback?.(), 12);
        },
        resize(cols, rows) {
          this.cols = cols;
          this.rows = rows;
          resizes.push([cols, rows]);
        },
        reset() { resets += 1; },
        refresh() {},
        scrollToBottom() {},
      };
      const tile = createBigScreenTile({
        workspace_id: 'layout-test',
        id: 'a',
        name: 'A',
      });
      tile.term = fakeTerm;
      tile.terminalEl.getBoundingClientRect = () => ({ width: 0, height: 0 });
      const frame = (streamId, streamEpoch, frameSeq, frameEnd, snapshot, cols = 80, rows = 24) => ({
        screen_frame_supported: true,
        screen_snapshot_delta_safe: true,
        stream_id: streamId,
        stream_epoch: streamEpoch,
        frame_seq: frameSeq,
        frame_end: frameEnd,
        history_start: 0,
        history_end: frameEnd,
        screen_snapshot: snapshot,
        screen_snapshot_end: frameEnd,
        screen_snapshot_cols: cols,
        screen_snapshot_rows: rows,
      });
      const delta = (streamId, streamEpoch, frameSeq, outputStart, outputEnd, data, extra = {}) => ({
        stream_id: streamId,
        stream_epoch: streamEpoch,
        frame_seq: frameSeq,
        output_start: outputStart,
        output_end: outputEnd,
        data,
        ...extra,
      });
      const waitFor = async predicate => {
        const deadline = performance.now() + 3000;
        while (performance.now() < deadline) {
          if (predicate()) return true;
          await new Promise(resolve => setTimeout(resolve, 10));
        }
        return false;
      };

      beginBigScreenAttach(tile);
      applyBigScreenFrame(tile, frame('big-stream-a', 100, 1, 10, 'FRAME_A'), { authoritative: true });
      acceptBigScreenDelta(tile, delta('big-stream-a', 100, 2, 10, 12, 'D2'));
      acceptBigScreenDelta(tile, delta('big-stream-a', 100, 3, 12, 12, '', {
        event_type: 'resize',
        cols: 100,
        rows: 30,
      }));
      acceptBigScreenDelta(tile, delta('big-stream-a', 100, 4, 12, 14, 'D4'));
      await waitFor(() => tile.frameAppliedSeq === 4);
      acceptBigScreenDelta(tile, delta('big-stream-a', 100, 4, 12, 14, 'D4'));

      const originalScreenRequest = backendApi.terminals.screen;
      let frameFetches = 0;
      let activeFrameFetches = 0;
      let maxFrameFetches = 0;
      const recoveryFrames = [
        { data: frame('big-stream-a', 100, 6, 16, 'GAP_FRAME'), delay: 0 },
        { data: frame('big-stream-a', 100, 7, 20, 'ALT_FRAME'), delay: 0 },
        { data: frame('big-stream-b', 200, 1, 14, 'NEW_STREAM_FRAME', 90, 28), delay: 0 },
        { data: frame('big-stream-b', 200, 4, 35, 'SPLIT_ALT_FRAME', 120, 36), delay: 0 },
        { data: frame('big-stream-b', 200, 6, 50, 'DIRTY_FRAME_6', 120, 36), delay: 40 },
        { data: frame('big-stream-b', 200, 8, 67, 'DIRTY_FRAME_8', 120, 36), delay: 0 },
        { data: frame('big-stream-b', 200, 9, 77, 'WRITE_FAILURE_FRAME', 120, 36), delay: 0 },
      ];
      backendApi.terminals.screen = () => {
        frameFetches += 1;
        activeFrameFetches += 1;
        maxFrameFetches = Math.max(maxFrameFetches, activeFrameFetches);
        const response = recoveryFrames.shift();
        return new Promise(resolve => setTimeout(() => {
          activeFrameFetches -= 1;
          resolve(response?.data);
        }, response?.delay || 0));
      };
      try {
        acceptBigScreenDelta(tile, delta('big-stream-a', 100, 6, 14, 16, 'GAP_RAW'));
        await waitFor(() => tile.frameAppliedSeq === 6 && tile.syncState === 'live');
        acceptBigScreenDelta(tile, delta('big-stream-a', 100, 7, 16, 20, '\x1b[?1049hALT_RAW'));
        await waitFor(() => tile.frameAppliedSeq === 7 && tile.syncState === 'live');
        acceptBigScreenDelta(tile, delta('big-stream-b', 200, 1, 0, 14, 'NEW_STREAM_RAW'));
        await waitFor(() => tile.frameStreamId === 'big-stream-b' && tile.frameAppliedSeq === 1);
        acceptBigScreenDelta(tile, delta('big-stream-b', 200, 2, 14, 14, '', {
          event_type: 'resize',
          cols: 120,
          rows: 36,
        }));
        await waitFor(() => tile.frameAppliedSeq === 2);
        acceptBigScreenDelta(tile, delta('big-stream-b', 200, 3, 14, 19, '\x1b[?10'));
        await waitFor(() => tile.frameAppliedSeq === 3);
        acceptBigScreenDelta(tile, delta('big-stream-b', 200, 4, 19, 35, '49hSPLIT_ALT_RAW'));
        await waitFor(() => tile.frameAppliedSeq === 4 && tile.syncState === 'live');
        acceptBigScreenDelta(tile, delta('big-stream-b', 200, 6, 37, 50, 'DIRTY_GAP_RAW'));
        acceptBigScreenDelta(tile, delta('big-stream-b', 200, 8, 52, 67, 'DIRTY_LATER_RAW'));
        await waitFor(() => tile.frameAppliedSeq === 8 && tile.syncState === 'live');
        failingWrite = 'FAIL_WRITE';
        acceptBigScreenDelta(tile, delta('big-stream-b', 200, 9, 67, 77, 'FAIL_WRITE'));
        await waitFor(() => tile.frameAppliedSeq === 9 && tile.syncState === 'live');
        acceptBigScreenDelta(tile, delta('big-stream-b', 200, 10, 77, 90, 'OLD_IN_FLIGHT'));
        const lowEpochDelta = 'LOW_EPOCH_DELTA';
        const lowEpochEnd = 1 + [...lowEpochDelta].length;
        applyBigScreenFrame(tile, frame('big-stream-c', 50, 1, 1, 'REATTACH_FRAME', 88, 26), {
          authoritative: true,
        });
        acceptBigScreenDelta(tile, delta(
          'big-stream-c',
          50,
          2,
          1,
          lowEpochEnd,
          lowEpochDelta,
        ));
        await waitFor(() => tile.frameStreamId === 'big-stream-c' && tile.frameAppliedSeq === 2);
        await new Promise(resolve => setTimeout(resolve, 250));
      } finally {
        backendApi.terminals.screen = originalScreenRequest;
        tile.syncState = 'disposed';
        if (tile.frameRecoveryTimer) clearTimeout(tile.frameRecoveryTimer);
      }
      return {
        writes,
        resizes,
        frameFetches,
        maxFrameFetches,
        resets,
        frameAppliedSeq: tile.frameAppliedSeq,
        frameAppliedEnd: tile.frameAppliedEnd,
        streamId: tile.frameStreamId,
        cols: tile.term.cols,
        rows: tile.term.rows,
      };
    });
    assert(
      bigScreenDeltaState.writes.join('|') === 'FRAME_A|D2|D4|GAP_FRAME|ALT_FRAME|NEW_STREAM_FRAME|\x1b[?10|SPLIT_ALT_FRAME|DIRTY_FRAME_6|DIRTY_FRAME_8|WRITE_FAILURE_FRAME|OLD_IN_FLIGHT|REATTACH_FRAME|LOW_EPOCH_DELTA'
        && bigScreenDeltaState.frameFetches === 7
        && bigScreenDeltaState.maxFrameFetches === 1
        && bigScreenDeltaState.resets === 9
        && bigScreenDeltaState.frameAppliedSeq === 2
        && bigScreenDeltaState.frameAppliedEnd === 16
        && bigScreenDeltaState.streamId === 'big-stream-c'
        && bigScreenDeltaState.cols === 88
        && bigScreenDeltaState.rows === 26,
      'big-screen ordered delta state machine lost, duplicated, or polled terminal output',
      bigScreenDeltaState,
    );

    await page.evaluate(() => {
      window.__terminalRenamePrompts = [];
      window.prompt = (message, value) => {
        window.__terminalRenamePrompts.push({ message, value });
        return '主开发终端';
      };
    });
    await clickTerminal(page, 'b');
    await page.waitForFunction(() => activeTerminalId === 'b');
    await clickTerminal(page, 'a');
    await page.waitForFunction(() => activeTerminalId === 'a');
    const promptsBeforeActiveClick = await page.evaluate(() => window.__terminalRenamePrompts.length);
    await clickTerminal(page, 'a');
    await page.waitForFunction(() => (
      document.querySelector('.terminal-tab[data-terminal-id="a"] .terminal-tab-name')?.textContent
        === '主开发终端'
    ));
    await page.evaluate(() => syncWorkspaceTerminals(activeWorkspaceId));
    const terminalRenameAudit = await page.evaluate(() => ({
      activeTerminalId,
      prompts: window.__terminalRenamePrompts,
      label: document.querySelector('.terminal-tab[data-terminal-id="a"] .terminal-tab-name')?.textContent,
    }));
    assert(
      promptsBeforeActiveClick === 0
        && terminalRenameAudit.activeTerminalId === 'a'
        && terminalRenameAudit.prompts.length === 1
        && terminalRenameAudit.prompts[0].value === 'A'
        && terminalRenameAudit.label === '主开发终端',
      'terminal tabs did not switch first and persist rename on the active click',
      terminalRenameAudit,
    );

    const stableBigScreenRender = await page.evaluate(async () => {
      disposeBigScreenTiles();
      const grid = document.getElementById('bigScreenGrid');
      const summaries = ['a', 'b'].map((id, index) => ({
        workspace_id: 'layout-test',
        id,
        name: id.toUpperCase(),
        alive: true,
        usable: true,
        _activity: { label: index ? 'IDLE' : 'OUTPUT', className: index ? 'idle' : 'output' },
      }));
      for (const summary of summaries) {
        const tile = createBigScreenTile(summary);
        tile.term = { dispose() {} };
        tile.socket = { disconnect() {} };
        bigScreenTiles.set(tile.key, tile);
        grid.appendChild(tile.el);
      }
      renderBigScreenTiles(summaries);
      await new Promise(resolve => setTimeout(resolve, 90));
      const originalChildren = Array.from(grid.children);
      const observer = new MutationObserver(() => {});
      observer.observe(grid, { childList: true });
      renderBigScreenTiles(summaries.map(summary => ({ ...summary })));
      renderBigScreenTiles([...summaries].reverse().map(summary => ({ ...summary })));
      await new Promise(resolve => setTimeout(resolve, 90));
      const mutations = observer.takeRecords();
      observer.disconnect();
      const currentChildren = Array.from(grid.children);
      const sameChildren = currentChildren.length === originalChildren.length
        && currentChildren.every((child, index) => child === originalChildren[index]);
      const visualOrder = summaries.map(summary => (
        bigScreenTiles.get(bigScreenTerminalKey(summary))?.el?.style?.order || ''
      ));
      const bigScreen = document.getElementById('terminalBigScreen');
      bigScreen.classList.add('active');
      bigScreen.setAttribute('aria-hidden', 'false');
      setBigScreenFocus('layout-test:a');
      await new Promise(resolve => setTimeout(resolve, 40));
      const gridRect = grid.getBoundingClientRect();
      const focusedRect = bigScreenTiles.get('layout-test:a').el.getBoundingClientRect();
      const titleRect = bigScreen.querySelector('.big-screen-title').getBoundingClientRect();
      const actionsRect = bigScreen.querySelector('.big-screen-actions').getBoundingClientRect();
      const firstFocused = {
        key: bigScreenFocusedKey,
        gridFocused: grid.classList.contains('focused'),
        visibleTiles: grid.querySelectorAll('.big-screen-tile.focused').length,
        controlsVisible: !document.getElementById('bigScreenFocusActions').hidden,
        fillsGrid: Math.abs(focusedRect.width - gridRect.width) <= 2
          && Math.abs(focusedRect.height - gridRect.height) <= 2,
        otherHidden: getComputedStyle(bigScreenTiles.get('layout-test:b').el).display === 'none',
        headerClear: actionsRect.top >= titleRect.bottom,
      };
      cycleBigScreenFocus(1);
      const nextFocusedKey = bigScreenFocusedKey;
      cycleBigScreenFocus(1);
      const wrappedFocusedKey = bigScreenFocusedKey;
      setBigScreenFocus('');
      const returnedToGrid = !grid.classList.contains('focused')
        && document.getElementById('bigScreenFocusActions').hidden;
      bigScreen.classList.remove('active');
      bigScreen.setAttribute('aria-hidden', 'true');
      disposeBigScreenTiles();
      const membershipCandidates = Array.from({ length: 10 }, (_, index) => ({
        workspace_id: 'layout-test',
        id: `member-${index + 1}`,
      }));
      for (const item of membershipCandidates.slice(0, 9)) {
        bigScreenTiles.set(bigScreenTerminalKey(item), { key: bigScreenTerminalKey(item) });
      }
      const reranked = [membershipCandidates[9], ...membershipCandidates.slice(0, 9).reverse()];
      const stableMembership = stableBigScreenTerminalSelection(reranked)
        .map(bigScreenTerminalKey);
      bigScreenTiles.clear();
      return {
        mutationCount: mutations.length,
        sameChildren,
        childCount: originalChildren.length,
        limit: BIG_SCREEN_LIMIT,
        visualOrder,
        firstFocused,
        nextFocusedKey,
        wrappedFocusedKey,
        returnedToGrid,
        stableMembership,
      };
    });
    assert(
      stableBigScreenRender.mutationCount === 0
        && stableBigScreenRender.sameChildren
        && stableBigScreenRender.childCount === stableBigScreenRender.limit
        && stableBigScreenRender.visualOrder.join(',') === '1,0'
        && stableBigScreenRender.firstFocused.key === 'layout-test:a'
        && stableBigScreenRender.firstFocused.gridFocused
        && stableBigScreenRender.firstFocused.visibleTiles === 1
        && stableBigScreenRender.firstFocused.controlsVisible
        && stableBigScreenRender.firstFocused.fillsGrid
        && stableBigScreenRender.firstFocused.otherHidden
        && stableBigScreenRender.firstFocused.headerClear
        && stableBigScreenRender.nextFocusedKey === 'layout-test:b'
        && stableBigScreenRender.wrappedFocusedKey === 'layout-test:a'
        && stableBigScreenRender.returnedToGrid
        && stableBigScreenRender.stableMembership.length === 9
        && !stableBigScreenRender.stableMembership.includes('layout-test:member-10'),
      'unchanged system stats detached or rebuilt big-screen terminal surfaces',
      stableBigScreenRender,
    );

    await page.evaluate(async () => {
      const waitFor = async (predicate, timeout = 2000) => {
        const deadline = performance.now() + timeout;
        while (performance.now() < deadline) {
          if (predicate()) return true;
          await new Promise(resolve => setTimeout(resolve, 20));
        }
        return false;
      };
      disposeBigScreenTiles();
      terminalFullscreen = true;
      const bigScreen = document.getElementById('terminalBigScreen');
      bigScreen.classList.add('active');
      bigScreen.setAttribute('aria-hidden', 'false');
      window.__bigScreenInteractiveEventStart = window.__terminalLayoutSocketEvents.length;
      renderBigScreenTiles([
        { workspace_id: 'layout-test', id: 'big-input-a', name: 'Big input A' },
        { workspace_id: 'layout-test', id: 'big-input-b', name: 'Big input B' },
      ]);
      const ready = await waitFor(() => Array.from(bigScreenTiles.values()).every(tile => (
        tile.socket?.connected && !tile.replaying
      )));
      if (!ready) throw new Error('big-screen view-only terminals did not attach');
    });

    await page.locator('.big-screen-tile').first().click();
    await page.waitForFunction(() => {
      const tile = bigScreenTiles.get('layout-test:big-input-a');
      return tile?.interactive
        && tile.inputReady
        && !tile.replaying
        && tile.terminalEl.contains(document.activeElement);
    });
    await new Promise(resolve => setTimeout(resolve, 120));
    const firstFocusedFont = await page.evaluate(() => (
      Number(bigScreenTiles.get('layout-test:big-input-a')?.term?.options?.fontSize || 0)
    ));
    await page.keyboard.type('FIRST SPACE');
    await page.keyboard.press('Enter');

    await page.locator('#bigScreenNextBtn').click();
    await page.waitForFunction(() => {
      const tile = bigScreenTiles.get('layout-test:big-input-b');
      return tile?.interactive
        && tile.inputReady
        && !tile.replaying
        && tile.terminalEl.contains(document.activeElement);
    });
    await new Promise(resolve => setTimeout(resolve, 120));
    const secondFocusedFont = await page.evaluate(() => (
      Number(bigScreenTiles.get('layout-test:big-input-b')?.term?.options?.fontSize || 0)
    ));
    await page.keyboard.type('SECOND SPACE');
    await page.keyboard.press('Enter');

    const bigScreenInputAudit = await page.evaluate(async () => {
      const start = Number(window.__bigScreenInteractiveEventStart || 0);
      const events = window.__terminalLayoutSocketEvents.slice(start);
      setBigScreenFocus('');
      await new Promise(resolve => setTimeout(resolve, 180));
      const gridFonts = [
        Number(bigScreenTiles.get('layout-test:big-input-a')?.term?.options?.fontSize || 0),
        Number(bigScreenTiles.get('layout-test:big-input-b')?.term?.options?.fontSize || 0),
      ];
      const result = {
        attachModes: events
          .filter(event => event.name === 'terminal_attach')
          .map(event => `${event.terminal}:${event.viewOnly ? 'read' : 'write'}`),
        inputA: events
          .filter(event => event.name === 'terminal_input' && event.terminal === 'big-input-a')
          .map(event => event.data)
          .join(''),
        inputB: events
          .filter(event => event.name === 'terminal_input' && event.terminal === 'big-input-b')
          .map(event => event.data)
          .join(''),
        gridFonts,
      };
      disposeBigScreenTiles();
      terminalFullscreen = false;
      const bigScreen = document.getElementById('terminalBigScreen');
      bigScreen.classList.remove('active');
      bigScreen.setAttribute('aria-hidden', 'true');
      return result;
    });
    assert(
      bigScreenInputAudit.attachModes.includes('big-input-a:write')
        && bigScreenInputAudit.attachModes.includes('big-input-a:read')
        && bigScreenInputAudit.attachModes.includes('big-input-b:write')
        && bigScreenInputAudit.inputA === 'FIRST SPACE\r'
        && bigScreenInputAudit.inputB === 'SECOND SPACE\r'
        && firstFocusedFont > bigScreenInputAudit.gridFonts[0]
        && Math.abs(firstFocusedFont - secondFocusedFont) <= 0.2
        && Math.abs(bigScreenInputAudit.gridFonts[0] - bigScreenInputAudit.gridFonts[1]) <= 0.2,
      'focused big-screen terminals were not writable or kept inconsistent font sizes after switching',
      { firstFocusedFont, secondFocusedFont, ...bigScreenInputAudit },
    );

    const bigScreenLegacyReplay = await page.evaluate(async () => {
      const writes = [];
      const tile = createBigScreenTile({
        workspace_id: 'layout-test',
        id: 'legacy',
        name: 'Legacy',
      });
      tile.term = {
        cols: 80,
        rows: 24,
        options: { fontSize: 11 },
        write(value, callback) {
          writes.push(String(value || ''));
          setTimeout(() => callback?.(), 8);
        },
        resize(cols, rows) {
          this.cols = cols;
          this.rows = rows;
        },
        reset() {},
        clear() {},
        refresh() {},
        scrollToBottom() {},
      };
      tile.terminalEl.getBoundingClientRect = () => ({ width: 0, height: 0 });
      const replay = 'R'.repeat(TERMINAL_REPLAY_CHUNK_CHARS + 7);
      const restoring = restoreBigScreenTile(tile, {
        screen_frame_supported: false,
        buffer: replay,
        history_end: replay.length,
      });
      await new Promise(resolve => setTimeout(resolve, 2));
      acceptBigScreenDelta(tile, { data: 'LIVE_AFTER_REPLAY' });
      await restoring;
      return {
        combined: writes.join(''),
        replayBoundary: TERMINAL_REPLAY_CHUNK_CHARS,
        replaying: tile.replaying,
        pendingOutput: tile.pendingOutput,
      };
    });
    assert(
      bigScreenLegacyReplay.combined.endsWith('R'.repeat(7) + 'LIVE_AFTER_REPLAY')
        && bigScreenLegacyReplay.combined.indexOf('LIVE_AFTER_REPLAY') >= bigScreenLegacyReplay.replayBoundary
        && !bigScreenLegacyReplay.replaying
        && bigScreenLegacyReplay.pendingOutput === '',
      'big-screen legacy replay interleaved live output with replay chunks',
      bigScreenLegacyReplay,
    );

    const replayOffsetCheck = await page.evaluate(() => {
      const view = {};
      setTerminalReplayWindow(view, '😀A', 10, 12);
      appendTerminalReplayWindow(view, '😀' + String.fromCharCode(27) + '[6nB', {
        start: 12,
        end: 14,
        historyStart: 0,
      });
      return {
        buffer: view.replayBuffer,
        units: view.replayUnits,
        start: view.replayStart,
        end: view.replayEnd,
      };
    });
    assert(
      replayOffsetCheck.buffer === '😀A😀B'
        && replayOffsetCheck.units === 4
        && replayOffsetCheck.start === 10
        && replayOffsetCheck.end === 14,
      'Unicode/control-sequence replay offsets drifted',
      replayOffsetCheck,
    );

    let start = 0;
    reports.push(await snapshot(page, 'narrow A', 'a', 'terminal_attach', start));

    const inputFilterAudit = await page.evaluate(() => {
      const esc = String.fromCharCode(27);
      const userText = 'IO Rc 普通输入不会被过滤。';
      const generated = esc + '[I' + esc + '[12;34R' + esc + '[?1;2c';
      return {
        plain: normalizeTerminalInput(userText),
        mixed: normalizeTerminalInput(generated + userText + esc + '[O'),
        generatedOnly: isGeneratedTerminalResponsePacket(generated),
        userOnly: isGeneratedTerminalResponsePacket(userText),
      };
    });
    assert(
      inputFilterAudit.plain === 'IO Rc 普通输入不会被过滤。'
        && inputFilterAudit.mixed === inputFilterAudit.plain
        && inputFilterAudit.generatedOnly
        && !inputFilterAudit.userOnly,
      'terminal response filtering removed user text or retained generated control replies',
      inputFilterAudit,
    );

    const fullscreenResizeAudit = await page.evaluate(async () => {
      const resizeEvents = () => window.__terminalLayoutSocketEvents.filter(event => (
        event.name === 'terminal_resize' || event.name === 'terminal_resize_workspace'
      ));
      const before = resizeEvents().length;
      setFullscreenTransitionPending(true);
      scheduleTerminalLayoutSync({ claimGeometry: true, forceEmit: true });
      await new Promise(resolve => setTimeout(resolve, 40));
      window.dispatchEvent(new Event('resize'));
      setFullscreenTransitionPending(false);
      scheduleTerminalLayoutSync({ claimGeometry: true, forceEmit: true });
      await new Promise(resolve => setTimeout(resolve, 120));
      const during = resizeEvents().length - before;
      await new Promise(resolve => setTimeout(resolve, FULLSCREEN_LAYOUT_SETTLE_MS + 100));
      const emitted = resizeEvents().slice(before);
      return {
        during,
        emitted,
        settleDelay: fullscreenLayoutSettleDelay(),
        transitionPending: fullscreenTransitionPending,
      };
    });
    assert(
      fullscreenResizeAudit.during === 0
        && fullscreenResizeAudit.emitted.length === 1
        && fullscreenResizeAudit.settleDelay === 0
        && !fullscreenResizeAudit.transitionPending,
      'fullscreen transition published an intermediate PTY geometry',
      fullscreenResizeAudit,
    );

    const inputDurabilityAudit = await page.evaluate(() => {
      const inputEvents = () => window.__terminalLayoutSocketEvents
        .filter(event => event.name === 'terminal_input');
      const view = activeTerminalView();

      const offlineStart = inputEvents().length;
      termSocket.connected = false;
      termConnected = false;
      termSocket.dispatch('disconnect');
      emitTerminalInput('OFFLINE_INPUT_A');
      emitTerminalInput('_OFFLINE_INPUT_B');
      const offlineQueued = view.pendingInput === 'OFFLINE_INPUT_A_OFFLINE_INPUT_B';
      const offlineNotSent = inputEvents().length === offlineStart;
      const expectedAttachSeq = activeAttachSeq;
      termSocket.connected = true;
      termConnected = true;
      const offlineFlushed = flushPendingTerminalInput(view);
      const offlineEvents = inputEvents().slice(offlineStart);

      const imeStart = inputEvents().length;
      const textarea = view.mount.querySelector('textarea.xterm-helper-textarea');
      const key = value => textarea.dispatchEvent(new KeyboardEvent('keydown', {
        key: value,
        bubbles: true,
        cancelable: true,
      }));
      key('。');
      key('。');
      textarea.dispatchEvent(new CompositionEvent('compositionstart', {
        bubbles: true,
        cancelable: true,
      }));
      textarea.dispatchEvent(new InputEvent('beforeinput', {
        data: '！',
        inputType: 'insertCompositionText',
        bubbles: true,
        cancelable: true,
      }));
      textarea.dispatchEvent(new CompositionEvent('compositionend', {
        data: '！',
        bubbles: true,
        cancelable: true,
      }));
      textarea.dispatchEvent(new CompositionEvent('compositionstart', {
        bubbles: true,
        cancelable: true,
      }));
      textarea.dispatchEvent(new CompositionEvent('compositionend', {
        data: '！',
        bubbles: true,
        cancelable: true,
      }));
      const imeEvents = inputEvents().slice(imeStart);

      return {
        offlineQueued,
        offlineNotSent,
        offlineFlushed,
        offlineEvents,
        expectedAttachSeq,
        imeData: imeEvents.map(event => event.data),
      };
    });
    assert(
      inputDurabilityAudit.offlineQueued
        && inputDurabilityAudit.offlineNotSent
        && inputDurabilityAudit.offlineFlushed
        && inputDurabilityAudit.offlineEvents.length === 1
        && inputDurabilityAudit.offlineEvents[0].data === 'OFFLINE_INPUT_A_OFFLINE_INPUT_B'
        && inputDurabilityAudit.offlineEvents[0].attachSeq === inputDurabilityAudit.expectedAttachSeq
        && inputDurabilityAudit.imeData.join('|') === '。|。|！|！',
      'offline input was lost or distinct IME punctuation commits were deduplicated',
      inputDurabilityAudit,
    );

    const promptNavigatorAudit = await page.evaluate(async () => {
      const waitFor = async (predicate, timeout = 2000) => {
        const deadline = performance.now() + timeout;
        while (performance.now() < deadline) {
          if (predicate()) return true;
          await new Promise(resolve => setTimeout(resolve, 20));
        }
        return false;
      };
      const view = activeTerminalView();
      const spacer = label => Array.from(
        { length: 22 },
        (_, index) => `${label}_ASSISTANT_${index}`,
      ).join('\r\n');
      const replay = [
        '› first navigation prompt',
        '  first navigation continuation',
        spacer('FIRST'),
        '› second navigation prompt',
        spacer('SECOND'),
        '› third navigation prompt',
        spacer('THIRD'),
      ].join('\r\n') + '\r\n';

      setTerminalReplayWindow(view, replay, 0, terminalTextUnitLength(replay));
      resetTerminalDisplay(view);
      await new Promise(resolve => {
        writeTerminalReplayBuffer(replay, resolve, activeAttachSeq, view, 'prompt-navigation-audit');
      });
      const initialReady = await waitFor(() => (
        document.querySelectorAll('.terminal-prompt-marker').length === 3
      ));
      const initialTexts = Array.from(document.querySelectorAll('.terminal-prompt-list-text'))
        .map(element => element.textContent);
      const initialActive = Number(
        document.querySelector('.terminal-prompt-marker.active')?.dataset.promptIndex ?? -1
      );
      const firstPromptLine = Number(view.promptNavigatorState?.entries?.[0]?.line ?? -1);
      const firstPromptCell = view.term.buffer.active.getLine(firstPromptLine)?.getCell(0);
      const firstContinuationCell = view.term.buffer.active.getLine(firstPromptLine + 1)?.getCell(2);
      const replayInputHighlighted = firstPromptCell?.getBgColor?.() === 238
        && firstContinuationCell?.getBgColor?.() === 238;
      const scannedLengthBeforeStream = Number(view.promptNavigatorState?.bufferLength || 0);

      appendTerminalOutput(view, '\r\nASSISTANT_STREAM_WITHOUT_PROMPT\r\n');
      await new Promise(resolve => setTimeout(resolve, 80));
      const streamDidNotScan = Number(view.promptNavigatorState?.bufferLength || 0)
        === scannedLengthBeforeStream;

      markTerminalPromptSubmissionScan(view, '\r');
      appendTerminalOutput(view, '› fourth navigation prompt\r\n');
      const submittedReady = await waitFor(() => (
        document.querySelectorAll('.terminal-prompt-marker').length === 4
      ));

      view.term.scrollToLine(0);
      await new Promise(resolve => setTimeout(resolve, 40));
      const firstActiveAfterScroll = Number(
        document.querySelector('.terminal-prompt-marker.active')?.dataset.promptIndex ?? -1
      ) === 0;

      const navigator = document.getElementById('terminalPromptNavigator');
      navigator.dispatchEvent(new PointerEvent('pointerenter'));
      await new Promise(resolve => setTimeout(resolve, 180));
      const popoverStyle = getComputedStyle(document.getElementById('terminalPromptPopover'));
      const detailsVisible = popoverStyle.visibility === 'visible'
        && Number(popoverStyle.opacity) > 0.9;

      const secondEntryLine = view.promptNavigatorState.entries[1].line;
      document.querySelector('.terminal-prompt-list-item[data-prompt-index="1"]')?.click();
      await new Promise(resolve => setTimeout(resolve, 40));
      const jumpedToSecond = Math.abs(
        Number(view.term.buffer.active.viewportY || 0) - secondEntryLine
      ) <= 2;
      navigator.dispatchEvent(new PointerEvent('pointerleave'));

      const rail = document.getElementById('terminalPromptRail');
      const list = document.getElementById('terminalPromptList');
      rail.style.maxHeight = '48px';
      list.style.maxHeight = '74px';
      rail.scrollTop = 0;
      list.scrollTop = 0;
      view.mount.querySelector('.xterm-viewport')?.dispatchEvent(new WheelEvent('wheel', {
        bubbles: true,
        cancelable: true,
        deltaMode: 0,
        deltaY: 64,
      }));
      await new Promise(resolve => setTimeout(resolve, 40));
      const terminalWheelSynced = rail.scrollTop > 0 && list.scrollTop > 0;
      rail.style.removeProperty('max-height');
      list.style.removeProperty('max-height');

      return {
        initialReady,
        initialTexts,
        initialActive,
        streamDidNotScan,
        submittedReady,
        firstActiveAfterScroll,
        detailsVisible,
        jumpedToSecond,
        replayInputHighlighted,
        terminalWheelSynced,
      };
    });
    assert(
      promptNavigatorAudit.initialReady
        && promptNavigatorAudit.initialTexts.join('|')
          === 'first navigation prompt|second navigation prompt|third navigation prompt'
        && promptNavigatorAudit.initialActive === 2
        && promptNavigatorAudit.streamDidNotScan
        && promptNavigatorAudit.submittedReady
        && promptNavigatorAudit.firstActiveAfterScroll
        && promptNavigatorAudit.detailsVisible
        && promptNavigatorAudit.jumpedToSecond
        && promptNavigatorAudit.replayInputHighlighted
        && promptNavigatorAudit.terminalWheelSynced,
      'prompt navigator or highlighted input synchronization did not behave correctly',
      promptNavigatorAudit,
    );

    const promptNavScreenshotDir = process.env.TERMINAL_PROMPT_NAV_SCREENSHOT_DIR;
    if (promptNavScreenshotDir) {
      mkdirSync(promptNavScreenshotDir, { recursive: true });
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.waitForTimeout(180);
      await page.mouse.move(0, 0);
      await page.screenshot({ path: join(promptNavScreenshotDir, 'desktop-collapsed.png') });
      await page.locator('.terminal-prompt-marker').last().hover();
      await page.waitForTimeout(180);
      await page.screenshot({ path: join(promptNavScreenshotDir, 'desktop-expanded.png') });

      await page.setViewportSize({ width: 390, height: 844 });
      await page.waitForTimeout(180);
      await page.locator('.terminal-prompt-marker').last().hover();
      await page.waitForTimeout(180);
      await page.screenshot({ path: join(promptNavScreenshotDir, 'mobile-expanded.png') });
      await page.setViewportSize({ width: 760, height: 760 });
      await page.mouse.move(0, 0);
      await page.waitForTimeout(180);
    }

    const collapsedReplayRecovered = await page.evaluate(async () => {
      const view = activeTerminalView();
      const esc = String.fromCharCode(27);
      const replay = (esc + '[2J' + esc + '[H' + '• Working 30').repeat(900);
      setTerminalReplayWindow(view, replay, 0, terminalTextUnitLength(replay));
      resetTerminalDisplay(view);
      await new Promise(resolve => {
        writeTerminalReplayBuffer(replay, resolve, activeAttachSeq, view, 'collapsed-replay');
      });
      await new Promise(resolve => setTimeout(resolve, 50));
      const visible = terminalBufferVisibleText(view);
      const allLines = [];
      for (let index = 0; index < view.term.buffer.active.length; index += 1) {
        allLines.push(view.term.buffer.active.getLine(index)?.translateToString(true) || '');
      }
      return {
        visible,
        fullText: allLines.join('\n'),
        lineCount: visible.split('\n').filter(line => line.trim()).length,
      };
    });
    assert(
      collapsedReplayRecovered.fullText.includes('已用文本模式恢复终端缓存')
        && collapsedReplayRecovered.lineCount > 3,
      'collapsed ANSI replay was accepted as a valid terminal screen',
      collapsedReplayRecovered,
    );

    const slowReplay = await page.evaluate(async () => {
      const view = activeTerminalView();
      const originalWrite = view.term.write.bind(view.term);
      const replay = '0123456789abcdef'.repeat(16384);
      setTerminalReplayWindow(view, replay, 0, terminalTextUnitLength(replay));
      resetTerminalDisplay(view);
      view.term.write = (data, callback) => originalWrite(data, () => {
        setTimeout(() => callback?.(), 400);
      });
      const startedAt = performance.now();
      await new Promise(resolve => {
        writeTerminalReplayBuffer(replay, resolve, activeAttachSeq, view, 'slow-replay');
      });
      const result = {
        elapsed: performance.now() - startedAt,
        hydrated: view.hydrated,
        replayEnd: view.replayEnd,
        renderedEnd: view.renderedEnd,
      };
      view.term.write = originalWrite;
      return result;
    });
    assert(
      slowReplay.elapsed >= 3000
        && slowReplay.hydrated
        && slowReplay.renderedEnd === slowReplay.replayEnd,
      'slow terminal replay was marked complete before every chunk rendered',
      slowReplay,
    );

    const checkpointSaved = await page.evaluate(async () => {
      const view = activeTerminalView();
      resetTerminalDisplay(view);
      view.replayStart = 0;
      view.replayEnd = 0;
      view.replayUnits = 0;
      view.replayBuffer = '';
      await new Promise(resolve => view.term.write('CHECKPOINT_SENTINEL', resolve));
      view.hydrated = true;
      view.renderedEnd = 0;
      const saved = await publishTerminalSnapshot(view, { force: true });
      disposeTerminalView(view.workspaceId, view.terminalId);
      return saved;
    });
    assert(checkpointSaved, 'terminal checkpoint was not acknowledged');

    start = await eventCount(page);
    const switchInputStart = start;
    const switchInputImmediate = await page.evaluate(() => {
      const prefix = 'SWITCH_PREFIX_MUST_SURVIVE';
      const button = document.querySelector('.terminal-tab[data-terminal-id="b"]');
      button.focus();
      button.click();
      const view = activeTerminalView();
      const helper = view?.mount?.querySelector('textarea.xterm-helper-textarea');
      emitTerminalInput(prefix);
      return {
        prefix,
        activeTerminalId,
        viewTerminal: view?.terminalId || '',
        focused: document.activeElement === helper,
        pendingInput: view?.pendingInput || '',
      };
    });
    reports.push(await snapshot(page, 'narrow B', 'b', 'terminal_attach', start));
    const switchInputFinal = await page.evaluate(({ start }) => ({
      activeTerminalId,
      pendingInput: activeTerminalView()?.pendingInput || '',
      events: window.__terminalLayoutSocketEvents.slice(start).filter(event => (
        event.name === 'terminal_input'
      )),
    }), { start: switchInputStart });
    assert(
      switchInputImmediate.activeTerminalId === 'b'
        && switchInputImmediate.viewTerminal === 'b'
        && switchInputImmediate.focused
        && switchInputImmediate.pendingInput === switchInputImmediate.prefix
        && switchInputFinal.activeTerminalId === 'b'
        && switchInputFinal.pendingInput === ''
        && switchInputFinal.events.length === 1
        && switchInputFinal.events[0].terminal === 'b'
        && switchInputFinal.events[0].data === switchInputImmediate.prefix,
      'terminal switch lost, misrouted, or mixed control replies into immediate input',
      { switchInputImmediate, switchInputFinal },
    );

    start = await eventCount(page);
    await clickTerminal(page, 'a');
    reports.push(await snapshot(page, 'narrow A restored', 'a', 'terminal_attach', start));
    const restoredText = await page.evaluate(() => terminalBufferVisibleText(activeTerminalView()));
    assert(
      restoredText.includes('CHECKPOINT_SENTINEL') && restoredText.includes('CHECKPOINT_DELTA'),
      'disposed xterm view was not restored from its checkpoint plus output delta',
      restoredText,
    );

    start = await eventCount(page);
    await page.setViewportSize({ width: 1600, height: 900 });
    reports.push(await snapshot(page, 'wide A after viewport resize', 'a', 'terminal_resize', start));
    assert(
      reports.at(-1).term.cols > reports[0].term.cols,
      'wide viewport did not increase the active terminal columns',
      reports,
    );

    start = await eventCount(page);
    await clickTerminal(page, 'b');
    reports.push(await snapshot(page, 'wide cached B', 'b', 'terminal_attach', start));

    start = await eventCount(page);
    await clickTerminal(page, 'a');
    reports.push(await snapshot(page, 'wide A restored', 'a', 'terminal_attach', start));

    const deltaRecovery = await page.evaluate(async () => {
      const waitFor = async (predicate, timeout = 6000) => {
        const deadline = performance.now() + timeout;
        while (performance.now() < deadline) {
          if (predicate()) return true;
          await new Promise(resolve => setTimeout(resolve, 20));
        }
        return false;
      };
      const fullText = view => {
        const lines = [];
        const buffer = view?.term?.buffer?.active;
        for (let index = 0; buffer && index < buffer.length; index += 1) {
          lines.push(buffer.getLine(index)?.translateToString(true) || '');
        }
        return lines.join('\n');
      };
      const units = value => [...String(value || '')].length;
      const ESC = String.fromCharCode(27);
      const CRLF = String.fromCharCode(13, 10);
      const screenRequestCount = () => window.__terminalLayoutSocketEvents
        .filter(event => event.name === 'terminal_screen_request').length;
      const output = ({ seq, start, data = '', eventType = 'output', cols = 0, rows = 0 }) => {
        const end = eventType === 'resize' ? start : start + units(data);
        termSocket.dispatch('terminal_output', {
          workspace_id: activeWorkspaceId,
          terminal_id: activeTerminalId,
          stream_id: 'delta-stream-a',
          stream_epoch: 1000,
          frame_seq: seq,
          output_start: start,
          output_end: end,
          history_start: 0,
          event_type: eventType,
          cols,
          rows,
          data,
        });
        return end;
      };
      const frame = ({ seq, end, snapshot }) => ({
        ok: true,
        screen_frame_supported: true,
        screen_snapshot_delta_safe: true,
        workspace_id: activeWorkspaceId,
        terminal_id: activeTerminalId,
        stream_id: 'delta-stream-a',
        stream_epoch: 1000,
        frame_seq: seq,
        frame_end: end,
        history_start: 0,
        history_end: end,
        screen_snapshot: snapshot,
        screen_snapshot_end: end,
        screen_snapshot_cols: 120,
        screen_snapshot_rows: 35,
      });

      const attachView = activeTerminalView();
      const originalAttachWrite = attachView.term.write.bind(attachView.term);
      attachView.term.write = (data, callback) => originalAttachWrite(data, () => {
        if (String(data || '').includes('ATTACH_BASE：参考：中文连续')) {
          setTimeout(() => callback?.(), 80);
        } else {
          callback?.();
        }
      });
      window.__terminalDeltaAttachScenario = true;
      attachTerminal(true);
      const inputAttachSeq = activeAttachSeq;
      const queuedBeforeReady = CRLF + 'ATTACH_BEFORE_READY：中文连续';
      const queuedDuringSnapshot = CRLF + 'ATTACH_DURING_SNAPSHOT：中文连续';
      await new Promise(resolve => setTimeout(resolve, 10));
      emitTerminalInput('INPUT_BEFORE_READY');
      const preReadyView = activeTerminalView();
      const preReadyState = {
        attaching: preReadyView.attaching,
        backlog: preReadyView.deltaBacklog?.length || 0,
        visible: fullText(preReadyView),
      };
      await new Promise(resolve => setTimeout(resolve, 35));
      emitTerminalInput('_INPUT_DURING_FRAME');
      const duringSnapshotState = {
        frameWriting: Boolean(preReadyView.frameWriting),
        backlog: preReadyView.deltaBacklog?.length || 0,
        visible: fullText(preReadyView),
      };
      let seq = 102;
      let end = 100 + units(queuedBeforeReady) + units(queuedDuringSnapshot);
      const attachComplete = await waitFor(() => {
        const view = activeTerminalView();
        const text = fullText(view);
        return view?.hydrated
          && !view.replaying
          && Number(view.renderedEnd) === end
          && text.includes('ATTACH_BASE：参考：中文连续')
          && text.includes('ATTACH_BEFORE_READY：中文连续')
          && text.includes('ATTACH_DURING_SNAPSHOT：中文连续');
      });
      const view = activeTerminalView();
      view.term.write = originalAttachWrite;
      const afterAttach = fullText(view);
      const queuedInputEvent = window.__terminalLayoutSocketEvents.find(event => (
        event.name === 'terminal_input'
        && event.data === 'INPUT_BEFORE_READY_INPUT_DURING_FRAME'
      ));
      const originalReset = view.term.reset.bind(view.term);
      let liveResetCount = 0;
      view.term.reset = (...args) => {
        liveResetCount += 1;
        return originalReset(...args);
      };

      output({ seq: 101, start: 100, data: queuedBeforeReady });
      output({
        seq: 102,
        start: 100 + units(queuedBeforeReady),
        data: queuedDuringSnapshot,
      });
      await new Promise(resolve => setTimeout(resolve, 100));
      const afterDuplicate = fullText(view);
      const duplicateBeforeCount = afterDuplicate.split('ATTACH_BEFORE_READY：中文连续').length - 1;
      const duplicateDuringCount = afterDuplicate.split('ATTACH_DURING_SNAPSHOT：中文连续').length - 1;

      let lastMarker = '';
      for (let index = 0; index < 125; index += 1) {
        seq += 1;
        lastMarker = `DELTA_${String(index).padStart(3, '0')}：参考：中文连续显示`;
        end = output({ seq, start: end, data: CRLF + lastMarker });
      }
      seq += 1;
      end = output({
        seq,
        start: end,
        eventType: 'resize',
        cols: 111,
        rows: 27,
      });
      const continuousComplete = await waitFor(() => (
        Number(view.renderedEnd) === end
        && Number(view.frameAppliedSeq) === seq
        && view.term.cols === 111
        && view.term.rows === 27
        && fullText(view).includes(lastMarker)
        && !view.deltaWriting
        && (!view.deltaQueue || view.deltaQueue.length === 0)
      ));
      const resizeApplied = Boolean(
        continuousComplete
        && view.term.cols === 111
        && view.term.rows === 27
        && Number(view.frameAppliedSeq) === seq
      );
      await new Promise(resolve => setTimeout(resolve, 1200));
      const afterContinuous = fullText(view);
      const proposedAfterResize = view.fit.proposeDimensions();
      const resizeGeometry = {
        cols: view.term.cols,
        rows: view.term.rows,
        proposedCols: proposedAfterResize?.cols || 0,
        proposedRows: proposedAfterResize?.rows || 0,
      };
      const requestsAfterContinuous = screenRequestCount();
      const resetsAfterContinuous = liveResetCount;

      const livePrompt = 'LIVE_DELTA_USER_INPUT';
      const liveContinuation = 'LIVE_DELTA_USER_INPUT_CONTINUATION';
      const livePromptData = CRLF
        + ESC + '[0;1;2;39;49m› ' + ESC + '[0;39;49m' + livePrompt + ESC + '[0m' + CRLF
        + ESC + '[0;39;49m  ' + liveContinuation + ESC + '[0m' + CRLF;
      seq += 1;
      end = output({ seq, start: end, data: livePromptData });
      const liveInputHighlighted = await waitFor(() => {
        const buffer = view.term.buffer.active;
        for (let index = 0; index < buffer.length; index += 1) {
          const line = buffer.getLine(index);
          if (!(line?.translateToString(true) || '').includes(livePrompt)) continue;
          const continuation = buffer.getLine(index + 1);
          return line.getCell(0)?.getBgColor?.() === 238
            && (continuation?.translateToString(true) || '').includes(liveContinuation)
            && continuation.getCell(2)?.getBgColor?.() === 238;
        }
        return false;
      });

      const missingData = CRLF + 'INTENTIONALLY_DROPPED_DELTA';
      const gapData = CRLF + 'GAP_PACKET_MUST_BE_REPLACED';
      const gapSeq = seq + 2;
      const gapStart = end + units(missingData);
      const gapEnd = gapStart + units(gapData);
      window.__queueTerminalScreenResponse(frame({
        seq: gapSeq,
        end: gapEnd,
        snapshot: window.__terminalSnapshot('GAP_RECOVERED：参考：中文连续显示'),
      }), 160);
      output({ seq: gapSeq, start: gapStart, data: gapData });
      output({ seq: gapSeq, start: gapStart, data: gapData });

      await new Promise(resolve => setTimeout(resolve, 30));
      const recoveryData = CRLF + 'RECOVERY_LIVE_DELTA：恢复期间输出已重接';
      const recoverySeq = gapSeq + 1;
      const recoveryEnd = output({ seq: recoverySeq, start: gapEnd, data: recoveryData });
      const gapRecovered = await waitFor(() => {
        const text = fullText(view);
        return Number(view.renderedEnd) === recoveryEnd
          && text.includes('GAP_RECOVERED：参考：中文连续显示')
          && text.includes('RECOVERY_LIVE_DELTA：恢复期间输出已重接')
          && !view.replaying
          && !view.deltaWriting;
      });
      const afterGap = fullText(view);
      const requestsAfterGap = screenRequestCount();
      seq = recoverySeq;
      end = recoveryEnd;

      const altEnterData = ESC + '[?1049h' + ESC + '[2J' + ESC + '[HALT_RAW_MUST_NOT_SURVIVE';
      const altEnterSeq = seq + 1;
      const altEnterEnd = end + units(altEnterData);
      window.__queueTerminalScreenResponse(frame({
        seq: altEnterSeq,
        end: altEnterEnd,
        snapshot: ESC + '[?1049h' + ESC + '[2J' + ESC + '[HALT_ENTER_RECOVERED：参考：中文连续显示',
      }));
      output({ seq: altEnterSeq, start: end, data: altEnterData });
      const altEntered = await waitFor(() => (
        screenRequestCount() === requestsAfterGap + 1
        && Number(view.renderedEnd) === altEnterEnd
        && fullText(view).includes('ALT_ENTER_RECOVERED：参考：中文连续显示')
      ));
      const afterAltEnter = fullText(view);
      seq = altEnterSeq;
      end = altEnterEnd;

      const altExitData = ESC + '[?1049l';
      const altExitSeq = seq + 1;
      const altExitEnd = end + units(altExitData);
      window.__queueTerminalScreenResponse(frame({
        seq: altExitSeq,
        end: altExitEnd,
        snapshot: window.__terminalSnapshot('ALT_EXIT_RECOVERED：参考：中文连续显示'),
      }));
      output({ seq: altExitSeq, start: end, data: altExitData });
      const altExited = await waitFor(() => (
        screenRequestCount() === requestsAfterGap + 2
        && Number(view.renderedEnd) === altExitEnd
        && fullText(view).includes('ALT_EXIT_RECOVERED：参考：中文连续显示')
      ));

      seq = altExitSeq;
      end = altExitEnd;
      const requestsBeforeReattach = screenRequestCount();
      const originalLiveWrite = view.term.write.bind(view.term);
      const inFlightData = CRLF + 'DELTA_IN_FLIGHT_WHEN_REATTACHING';
      view.term.write = (data, callback) => originalLiveWrite(data, () => {
        if (String(data || '').includes('DELTA_IN_FLIGHT_WHEN_REATTACHING')) {
          setTimeout(() => callback?.(), 40);
        } else {
          callback?.();
        }
      });
      seq += 1;
      end = output({ seq, start: end, data: inFlightData });
      await waitFor(() => Boolean(view.deltaWriting), 1000);
      await new Promise(resolve => setTimeout(resolve, 10));
      window.__terminalReattachScenario = {
        seq,
        end,
        delay: 500,
        text: 'REATTACH_AUTHORITATIVE：等待 attach 接管旧写入',
      };
      attachTerminal(true);
      const reattachComplete = await waitFor(() => (
        !view.attaching
        && view.hydrated
        && !view.replaying
        && Number(view.renderedEnd) === end
        && fullText(view).includes('REATTACH_AUTHORITATIVE：等待 attach 接管旧写入')
      ));
      await new Promise(resolve => setTimeout(resolve, 250));
      view.term.write = originalLiveWrite;
      const requestsAfterReattach = screenRequestCount();

      return {
        attachComplete,
        queuedInputAttachSeq: queuedInputEvent?.attachSeq ?? null,
        expectedInputAttachSeq: inputAttachSeq,
        continuousComplete,
        liveInputHighlighted,
        gapRecovered,
        altEntered,
        altExited,
        reattachComplete,
        requestsBeforeReattach,
        requestsAfterReattach,
        preReadyState,
        duringSnapshotState,
        afterAttach,
        duplicateBeforeCount,
        duplicateDuringCount,
        afterContinuous: afterContinuous.slice(-6000),
        resizeApplied,
        resizeGeometry,
        requestsAfterContinuous,
        resetsAfterContinuous,
        afterGap,
        requestsAfterGap,
        afterAltEnter,
        finalText: fullText(view),
        finalRenderedEnd: view.renderedEnd,
        finalAcceptedEnd: view.deltaAcceptedEnd,
        finalAcceptedSeq: view.deltaAcceptedSeq,
        expectedEnd: end,
        expectedSeq: seq,
        totalScreenRequests: screenRequestCount(),
        queuedScreenResponses: window.__terminalScreenResponses.length,
      };
    });
    assert(
      deltaRecovery.attachComplete
        && deltaRecovery.queuedInputAttachSeq === deltaRecovery.expectedInputAttachSeq
        && deltaRecovery.preReadyState.attaching
        && deltaRecovery.preReadyState.backlog === 1
        && !deltaRecovery.preReadyState.visible.includes('ATTACH_BEFORE_READY：中文连续')
        && deltaRecovery.duringSnapshotState.frameWriting
        && deltaRecovery.duringSnapshotState.backlog === 2
        && !deltaRecovery.duringSnapshotState.visible.includes('ATTACH_DURING_SNAPSHOT：中文连续')
        && deltaRecovery.afterAttach.includes('ATTACH_BASE：参考：中文连续')
        && deltaRecovery.afterAttach.includes('ATTACH_BEFORE_READY：中文连续')
        && deltaRecovery.afterAttach.includes('ATTACH_DURING_SNAPSHOT：中文连续')
        && deltaRecovery.duplicateBeforeCount === 1
        && deltaRecovery.duplicateDuringCount === 1
        && deltaRecovery.continuousComplete
        && deltaRecovery.liveInputHighlighted
        && deltaRecovery.afterContinuous.includes('DELTA_124：参考：中文连续显示')
        && !deltaRecovery.afterContinuous.includes('参 考 ： 中 文 连 续 显 示')
        && deltaRecovery.resizeApplied
        && deltaRecovery.resizeGeometry.cols === deltaRecovery.resizeGeometry.proposedCols
        && deltaRecovery.resizeGeometry.rows === deltaRecovery.resizeGeometry.proposedRows
        && deltaRecovery.requestsAfterContinuous === 0
        && deltaRecovery.resetsAfterContinuous === 0
        && deltaRecovery.gapRecovered
        && deltaRecovery.afterGap.includes('GAP_RECOVERED：参考：中文连续显示')
        && deltaRecovery.afterGap.includes('RECOVERY_LIVE_DELTA：恢复期间输出已重接')
        && !deltaRecovery.afterGap.includes('GAP_PACKET_MUST_BE_REPLACED')
        && deltaRecovery.requestsAfterGap === 1
        && deltaRecovery.altEntered
        && deltaRecovery.afterAltEnter.includes('ALT_ENTER_RECOVERED：参考：中文连续显示')
        && !deltaRecovery.afterAltEnter.includes('ALT_RAW_MUST_NOT_SURVIVE')
        && deltaRecovery.altExited
        && deltaRecovery.reattachComplete
        && deltaRecovery.finalText.includes('REATTACH_AUTHORITATIVE：等待 attach 接管旧写入')
        && deltaRecovery.requestsAfterReattach === deltaRecovery.requestsBeforeReattach
        && deltaRecovery.finalRenderedEnd === deltaRecovery.expectedEnd
        && deltaRecovery.finalAcceptedEnd === deltaRecovery.expectedEnd
        && deltaRecovery.finalAcceptedSeq === deltaRecovery.expectedSeq
        && deltaRecovery.totalScreenRequests === 3
        && deltaRecovery.queuedScreenResponses === 0,
      'event-driven terminal deltas did not preserve continuity or recover exactly on demand',
      deltaRecovery,
    );
    console.log(
      'PASS: event-driven terminal sync queues attach output, applies 125+ deltas without polling/reset,'
      + ' recovers one gap with one request, and recovers alternate-buffer transitions',
    );

    const reducerAudit = await page.evaluate(async () => {
      const waitFor = async (predicate, timeout = 2000) => {
        const deadline = performance.now() + timeout;
        while (performance.now() < deadline) {
          if (predicate()) return true;
          await new Promise(resolve => setTimeout(resolve, 20));
        }
        return false;
      };
      const screenRequestCount = () => window.__terminalLayoutSocketEvents
        .filter(event => event.name === 'terminal_screen_request').length;
      const terminalInputEvents = () => window.__terminalLayoutSocketEvents
        .filter(event => event.name === 'terminal_input');
      const view = activeTerminalView();
      const frame = ({ streamId, streamEpoch, seq, end, snapshot, cols = 120, rows = 35 }) => ({
        ok: true,
        screen_frame_supported: true,
        screen_snapshot_delta_safe: true,
        workspace_id: view.workspaceId,
        terminal_id: view.terminalId,
        stream_id: streamId,
        stream_epoch: streamEpoch,
        frame_seq: seq,
        frame_end: end,
        history_start: 0,
        history_end: end,
        screen_snapshot: snapshot,
        screen_snapshot_end: end,
        screen_snapshot_cols: cols,
        screen_snapshot_rows: rows,
      });

      const originalDuplicateText = 'DUPLICATE_BASELINE：中文连续显示\r\n';
      const originalDuplicateSeq = Number(view.deltaAcceptedSeq) + 1;
      const originalDuplicateStart = Number(view.deltaAcceptedEnd);
      const originalDuplicateEnd = originalDuplicateStart + originalDuplicateText.length;
      termSocket.dispatch('terminal_output', {
        workspace_id: view.workspaceId,
        terminal_id: view.terminalId,
        stream_id: view.frameStreamId,
        stream_epoch: view.frameStreamEpoch,
        frame_seq: originalDuplicateSeq,
        output_start: originalDuplicateStart,
        output_end: originalDuplicateEnd,
        history_start: 0,
        data: originalDuplicateText,
      });
      const originalDuplicateApplied = await waitFor(() => (
        Number(view.frameAppliedSeq) === originalDuplicateSeq
      ));
      const acceptedSeq = Number(view.deltaAcceptedSeq);
      const acceptedEnd = Number(view.deltaAcceptedEnd);
      const requestsBeforeConflict = screenRequestCount();
      window.__queueTerminalScreenResponse(frame({
        streamId: view.frameStreamId,
        streamEpoch: view.frameStreamEpoch,
        seq: acceptedSeq,
        end: acceptedEnd,
        snapshot: window.__terminalSnapshot('CONFLICT_DUPLICATE_RECOVERED'),
      }));
      termSocket.dispatch('terminal_output', {
        workspace_id: view.workspaceId,
        terminal_id: view.terminalId,
        stream_id: view.frameStreamId,
        stream_epoch: view.frameStreamEpoch,
        frame_seq: acceptedSeq,
        output_start: originalDuplicateStart,
        output_end: acceptedEnd,
        history_start: 0,
        data: 'CONFLICTING_DUPLICATE',
      });
      const conflictRecovered = await waitFor(() => (
        screenRequestCount() === requestsBeforeConflict + 1
        && terminalBufferVisibleText(view).includes('CONFLICT_DUPLICATE_RECOVERED')
      ), 1200);
      if (!conflictRecovered) window.__terminalScreenResponses.length = 0;
      const conflictRequestDelta = screenRequestCount() - requestsBeforeConflict;

      const ESC = String.fromCharCode(27);
      const inputsBeforeFrame = terminalInputEvents().length;
      const generatedFrameSeq = Number(view.deltaAcceptedSeq) + 1;
      const generatedFrameEnd = Number(view.deltaAcceptedEnd);
      const frameApplied = await queueTerminalScreenFrame(view, frame({
        streamId: view.frameStreamId,
        streamEpoch: view.frameStreamEpoch,
        seq: generatedFrameSeq,
        end: generatedFrameEnd,
        snapshot: window.__terminalSnapshot('FRAME_REPLAY_DSR') + ESC + '[6n',
      }), { authoritative: true, reason: 'generated-response audit' });
      await new Promise(resolve => setTimeout(resolve, 80));
      const inputsAfterFrame = terminalInputEvents().length;

      const liveSeq = Number(view.deltaAcceptedSeq) + 1;
      const liveEnd = Number(view.deltaAcceptedEnd);
      termSocket.dispatch('terminal_output', {
        workspace_id: view.workspaceId,
        terminal_id: view.terminalId,
        stream_id: view.frameStreamId,
        stream_epoch: view.frameStreamEpoch,
        frame_seq: liveSeq,
        output_start: liveEnd,
        output_end: liveEnd,
        history_start: 0,
        data: ESC + '[6n',
      });
      const liveApplied = await waitFor(() => Number(view.frameAppliedSeq) === liveSeq);
      await new Promise(resolve => setTimeout(resolve, 80));
      const inputsAfterLive = terminalInputEvents().length;

      const backlogConflictSeq = Number(view.deltaAcceptedSeq) + 1;
      const backlogConflictStart = Number(view.deltaAcceptedEnd);
      const backlogConflictA = 'BACKLOG_CONFLICT_A';
      const backlogConflictB = 'BACKLOG_CONFLICT_B';
      const backlogConflictEnd = backlogConflictStart + [...backlogConflictA].length;
      const requestsBeforeBacklogConflict = screenRequestCount();
      window.__queueTerminalScreenResponse(frame({
        streamId: view.frameStreamId,
        streamEpoch: view.frameStreamEpoch,
        seq: backlogConflictSeq,
        end: backlogConflictEnd,
        snapshot: window.__terminalSnapshot('BACKLOG_CONFLICT_RECOVERED'),
      }));
      pauseTerminalDeltaStream(view, 'backlog conflict audit');
      for (const value of [backlogConflictA, backlogConflictB]) {
        termSocket.dispatch('terminal_output', {
          workspace_id: view.workspaceId,
          terminal_id: view.terminalId,
          stream_id: view.frameStreamId,
          stream_epoch: view.frameStreamEpoch,
          frame_seq: backlogConflictSeq,
          output_start: backlogConflictStart,
          output_end: backlogConflictEnd,
          history_start: 0,
          data: value,
        });
      }
      const backlogReconciled = reconcileTerminalDeltaBacklog(view);
      const backlogConflictRecovered = await waitFor(() => (
        screenRequestCount() === requestsBeforeBacklogConflict + 1
        && !view.deltaPaused
        && terminalBufferVisibleText(view).includes('BACKLOG_CONFLICT_RECOVERED')
      ));
      const backlogConflictText = terminalBufferVisibleText(view);

      const highEpochFrameApplied = await queueTerminalScreenFrame(view, frame({
        streamId: 'old-high-epoch-stream',
        streamEpoch: 9000,
        seq: 1,
        end: 400,
        snapshot: window.__terminalSnapshot('OLD_HIGH_EPOCH_FRAME'),
      }), { authoritative: true, reason: 'old backend stream audit' });
      const oldBacklogText = 'OLD_HIGH_EPOCH_BACKLOG';
      const lowEpochDeltaText = 'LOW_EPOCH_DELTA：中文连续显示';
      const lowEpochFrameEnd = 30;
      const lowEpochDeltaEnd = lowEpochFrameEnd + [...lowEpochDeltaText].length;
      pauseTerminalDeltaStream(view, 'lower epoch authoritative takeover audit');
      bufferTerminalDelta(view, normalizeTerminalDelta({
        stream_id: 'old-high-epoch-stream',
        stream_epoch: 9000,
        frame_seq: 2,
        output_start: 400,
        output_end: 400 + [...oldBacklogText].length,
        history_start: 0,
        data: oldBacklogText,
      }));
      bufferTerminalDelta(view, normalizeTerminalDelta({
        stream_id: 'new-low-epoch-stream',
        stream_epoch: 7,
        frame_seq: 2,
        output_start: lowEpochFrameEnd,
        output_end: lowEpochDeltaEnd,
        history_start: 0,
        data: lowEpochDeltaText,
      }));

      const originalRecoveryWrite = view.term.write.bind(view.term);
      view.term.write = (data, callback) => originalRecoveryWrite(data, () => {
        const delay = String(data || '').includes('LOW_EPOCH_FRAME') ? 140 : 0;
        setTimeout(() => callback?.(), delay);
      });
      const recoveryInput = 'RECOVERY_INPUT：帧回放期间输入不丢';
      const recoveryInputsBefore = terminalInputEvents().length;
      const recoveryAttachSeq = activeAttachSeq;
      const lowerEpochFramePromise = queueTerminalScreenFrame(view, frame({
        streamId: 'new-low-epoch-stream',
        streamEpoch: 7,
        seq: 1,
        end: lowEpochFrameEnd,
        snapshot: window.__terminalSnapshot('LOW_EPOCH_FRAME'),
      }), { authoritative: true, reason: 'new backend stream audit' });
      const lowerEpochFrameStarted = await waitFor(() => Boolean(view.frameWriting), 500);
      emitTerminalInput(recoveryInput);
      await new Promise(resolve => setTimeout(resolve, 40));
      const recoveryInputHeld = terminalInputEvents().length === recoveryInputsBefore
        && view.pendingInput === recoveryInput;
      const lowerEpochFrameApplied = await lowerEpochFramePromise;
      const lowerEpochDeltaApplied = await waitFor(() => (
        view.frameStreamId === 'new-low-epoch-stream'
        && Number(view.frameStreamEpoch) === 7
        && Number(view.frameAppliedSeq) === 2
        && Number(view.frameAppliedEnd) === lowEpochDeltaEnd
      ));
      const lowerEpochText = terminalBufferVisibleText(view);
      const recoveryInputEvents = terminalInputEvents().slice(recoveryInputsBefore);
      const recoveryInputFlushed = recoveryInputEvents.length === 1
        && recoveryInputEvents[0].data === recoveryInput
        && recoveryInputEvents[0].attachSeq === recoveryAttachSeq
        && view.pendingInput === '';
      view.term.write = originalRecoveryWrite;

      const disposable = createTerminalView(activeWorkspaceId, 'reducer-dispose-audit');
      const originalWrite = disposable.term.write.bind(disposable.term);
      disposable.term.write = (data, callback) => originalWrite(data, () => {
        setTimeout(() => callback?.(), 300);
      });
      const disposableFrame = (seq, marker) => ({
        screen_frame_supported: true,
        screen_snapshot_delta_safe: true,
        stream_id: 'dispose-audit-stream',
        stream_epoch: 5000,
        frame_seq: seq,
        frame_end: seq,
        history_start: 0,
        history_end: seq,
        screen_snapshot: window.__terminalSnapshot(marker),
        screen_snapshot_end: seq,
        screen_snapshot_cols: 80,
        screen_snapshot_rows: 24,
      });
      queueTerminalScreenFrame(disposable, disposableFrame(1, 'DISPOSE_FRAME_1'), {
        authoritative: true,
      }).catch(() => false);
      await waitFor(() => Boolean(disposable.frameWriting), 500);
      const pendingPromise = queueTerminalScreenFrame(disposable, disposableFrame(2, 'DISPOSE_FRAME_2'), {
        authoritative: true,
      });
      await waitFor(() => Boolean(disposable.pendingFrame), 500);
      disposeTerminalView(disposable.workspaceId, disposable.terminalId);
      const disposeReleased = Boolean(
        disposable.disposed
        && disposable.term === null
        && disposable.fit === null
        && disposable.serialize === null
        && disposable.mount === null
        && disposable.replayBuffer === ''
        && disposable.pendingOutput === ''
        && disposable.pendingInput === ''
      );
      const disposePendingResult = await Promise.race([
        pendingPromise.then(value => ({ settled: true, value })),
        new Promise(resolve => setTimeout(() => resolve({ settled: false }), 150)),
      ]);

      return {
        originalDuplicateApplied,
        conflictRecovered,
        conflictRequestDelta,
        frameApplied,
        generatedInputsDuringFrame: inputsAfterFrame - inputsBeforeFrame,
        liveApplied,
        generatedInputsDuringLiveDelta: inputsAfterLive - inputsAfterFrame,
        backlogReconciled,
        backlogConflictRecovered,
        backlogConflictRequestDelta: screenRequestCount() - requestsBeforeBacklogConflict,
        backlogConflictRawAbsent: !backlogConflictText.includes(backlogConflictA)
          && !backlogConflictText.includes(backlogConflictB),
        highEpochFrameApplied,
        lowerEpochFrameStarted,
        recoveryInputHeld,
        lowerEpochFrameApplied,
        lowerEpochDeltaApplied,
        lowerEpochFrameVisible: lowerEpochText.includes('LOW_EPOCH_FRAME'),
        lowerEpochDeltaVisible: lowerEpochText.includes(lowEpochDeltaText),
        oldEpochBacklogAbsent: !lowerEpochText.includes(oldBacklogText),
        recoveryInputFlushed,
        disposeReleased,
        disposePendingResult,
      };
    });
    assert(
      reducerAudit.originalDuplicateApplied
        && reducerAudit.conflictRecovered
        && reducerAudit.conflictRequestDelta === 1
        && reducerAudit.frameApplied
        && reducerAudit.generatedInputsDuringFrame === 0
        && reducerAudit.liveApplied
        && reducerAudit.generatedInputsDuringLiveDelta === 0
        && reducerAudit.backlogReconciled === false
        && reducerAudit.backlogConflictRecovered
        && reducerAudit.backlogConflictRequestDelta === 1
        && reducerAudit.backlogConflictRawAbsent
        && reducerAudit.highEpochFrameApplied
        && reducerAudit.lowerEpochFrameStarted
        && reducerAudit.recoveryInputHeld
        && reducerAudit.lowerEpochFrameApplied
        && reducerAudit.lowerEpochDeltaApplied
        && reducerAudit.lowerEpochFrameVisible
        && reducerAudit.lowerEpochDeltaVisible
        && reducerAudit.oldEpochBacklogAbsent
        && reducerAudit.recoveryInputFlushed
        && reducerAudit.disposeReleased
        && reducerAudit.disposePendingResult.settled
        && reducerAudit.disposePendingResult.value === false,
      'terminal reducer accepted a conflicting duplicate, leaked replay responses, or stranded dispose waiters',
      reducerAudit,
    );

    const recoveryEdgeAudit = await page.evaluate(async () => {
      const waitFor = async (predicate, timeout = 4000) => {
        const deadline = performance.now() + timeout;
        while (performance.now() < deadline) {
          if (predicate()) return true;
          await new Promise(resolve => setTimeout(resolve, 20));
        }
        return false;
      };
      const units = value => [...String(value || '')].length;
      const requestCount = () => window.__terminalLayoutSocketEvents
        .filter(event => event.name === 'terminal_screen_request').length;
      const view = activeTerminalView();
      const frame = (seq, end, marker) => ({
        ok: true,
        screen_frame_supported: true,
        screen_snapshot_delta_safe: true,
        workspace_id: view.workspaceId,
        terminal_id: view.terminalId,
        stream_id: view.frameStreamId,
        stream_epoch: view.frameStreamEpoch,
        frame_seq: seq,
        frame_end: end,
        history_start: 0,
        history_end: end,
        screen_snapshot: window.__terminalSnapshot(marker),
        screen_snapshot_end: end,
        screen_snapshot_cols: 120,
        screen_snapshot_rows: 35,
      });
      const output = (seq, start, data) => {
        const end = start + units(data);
        termSocket.dispatch('terminal_output', {
          workspace_id: view.workspaceId,
          terminal_id: view.terminalId,
          stream_id: view.frameStreamId,
          stream_epoch: view.frameStreamEpoch,
          frame_seq: seq,
          output_start: start,
          output_end: end,
          history_start: 0,
          data,
        });
        return end;
      };

      let seq = Number(view.deltaAcceptedSeq);
      let end = Number(view.deltaAcceptedEnd);
      const requestsBeforeRetry = requestCount();
      const missing = 'RETRY_INTENTIONALLY_MISSING';
      const retryRaw = 'RETRY_GAP_RAW_MUST_NOT_APPEAR';
      const retrySeq = seq + 2;
      const retryStart = end + units(missing);
      const retryEnd = retryStart + units(retryRaw);
      window.__queueTerminalScreenResponse({ error: 'planned retry failure' }, 0, 503);
      window.__queueTerminalScreenResponse(frame(
        retrySeq,
        retryEnd,
        'HTTP_RETRY_RECOVERED：第二次请求恢复成功',
      ));
      output(retrySeq, retryStart, retryRaw);
      const retryRecovered = await waitFor(() => (
        requestCount() === requestsBeforeRetry + 2
        && !view.deltaPaused
        && Number(view.renderedEnd) === retryEnd
        && terminalBufferVisibleText(view).includes('HTTP_RETRY_RECOVERED：第二次请求恢复成功')
      ));
      await new Promise(resolve => setTimeout(resolve, 120));
      const retryRequestDelta = requestCount() - requestsBeforeRetry;
      const retryText = terminalBufferVisibleText(view);
      seq = retrySeq;
      end = retryEnd;

      const ESC = String.fromCharCode(27);
      const splitPrefix = ESC + '[?10';
      const splitPrefixSeq = seq + 1;
      const splitPrefixEnd = output(splitPrefixSeq, end, splitPrefix);
      const splitPrefixApplied = await waitFor(() => (
        Number(view.frameAppliedSeq) === splitPrefixSeq
        && Number(view.renderedEnd) === splitPrefixEnd
      ));
      const requestsBeforeSplit = requestCount();
      const splitSuffix = '49hSPLIT_ALT_RAW_MUST_NOT_APPEAR';
      const splitSuffixSeq = splitPrefixSeq + 1;
      const splitSuffixEnd = splitPrefixEnd + units(splitSuffix);
      window.__queueTerminalScreenResponse(frame(
        splitSuffixSeq,
        splitSuffixEnd,
        'SPLIT_ALT_RECOVERED：跨 delta 控制序列已恢复',
      ));
      output(splitSuffixSeq, splitPrefixEnd, splitSuffix);
      const splitRecovered = await waitFor(() => (
        requestCount() === requestsBeforeSplit + 1
        && !view.deltaPaused
        && Number(view.renderedEnd) === splitSuffixEnd
        && terminalBufferVisibleText(view).includes('SPLIT_ALT_RECOVERED：跨 delta 控制序列已恢复')
      ));
      await new Promise(resolve => setTimeout(resolve, 120));
      const splitRequestDelta = requestCount() - requestsBeforeSplit;
      const splitText = terminalBufferVisibleText(view);

      const requestsBeforeInvalidOffset = requestCount();
      const invalidOffsetRaw = 'INVALID_OFFSET_RAW_MUST_NOT_APPEAR';
      const invalidOffsetSeq = splitSuffixSeq + 1;
      const invalidOffsetEnd = splitSuffixEnd + units(invalidOffsetRaw);
      window.__queueTerminalScreenResponse(frame(
        invalidOffsetSeq,
        invalidOffsetEnd,
        'INVALID_OFFSET_RECOVERED：载荷偏移不一致已恢复',
      ));
      termSocket.dispatch('terminal_output', {
        workspace_id: view.workspaceId,
        terminal_id: view.terminalId,
        stream_id: view.frameStreamId,
        stream_epoch: view.frameStreamEpoch,
        frame_seq: invalidOffsetSeq,
        output_start: splitSuffixEnd,
        output_end: splitSuffixEnd + 1,
        history_start: 0,
        data: invalidOffsetRaw,
      });
      const invalidOffsetRecovered = await waitFor(() => (
        requestCount() === requestsBeforeInvalidOffset + 1
        && !view.deltaPaused
        && Number(view.renderedEnd) === invalidOffsetEnd
        && terminalBufferVisibleText(view).includes('INVALID_OFFSET_RECOVERED：载荷偏移不一致已恢复')
      ));
      await new Promise(resolve => setTimeout(resolve, 120));
      const invalidOffsetRequestDelta = requestCount() - requestsBeforeInvalidOffset;
      const invalidOffsetText = terminalBufferVisibleText(view);

      return {
        retryRecovered,
        retryRequestDelta,
        retryRawAbsent: !retryText.includes(retryRaw),
        splitPrefixApplied,
        splitRecovered,
        splitRequestDelta,
        splitRawAbsent: !splitText.includes('SPLIT_ALT_RAW_MUST_NOT_APPEAR'),
        invalidOffsetRecovered,
        invalidOffsetRequestDelta,
        invalidOffsetRawAbsent: !invalidOffsetText.includes(invalidOffsetRaw),
      };
    });
    assert(
      recoveryEdgeAudit.retryRecovered
        && recoveryEdgeAudit.retryRequestDelta === 2
        && recoveryEdgeAudit.retryRawAbsent
        && recoveryEdgeAudit.splitPrefixApplied
        && recoveryEdgeAudit.splitRecovered
        && recoveryEdgeAudit.splitRequestDelta === 1
        && recoveryEdgeAudit.splitRawAbsent
        && recoveryEdgeAudit.invalidOffsetRecovered
        && recoveryEdgeAudit.invalidOffsetRequestDelta === 1
        && recoveryEdgeAudit.invalidOffsetRawAbsent,
      'terminal recovery did not retry HTTP failures or detect a split alternate-buffer transition',
      recoveryEdgeAudit,
    );

    const frameRaceAudit = await page.evaluate(async () => {
      const waitFor = async (predicate, timeout = 3000) => {
        const deadline = performance.now() + timeout;
        while (performance.now() < deadline) {
          if (predicate()) return true;
          await new Promise(resolve => setTimeout(resolve, 20));
        }
        return false;
      };
      const requestCount = () => window.__terminalLayoutSocketEvents
        .filter(event => event.name === 'terminal_screen_request').length;
      const view = activeTerminalView();
      const frame = (seq, end, marker) => ({
        ok: true,
        screen_frame_supported: true,
        screen_snapshot_delta_safe: true,
        workspace_id: view.workspaceId,
        terminal_id: view.terminalId,
        stream_id: view.frameStreamId,
        stream_epoch: view.frameStreamEpoch,
        frame_seq: seq,
        frame_end: end,
        history_start: 0,
        history_end: end,
        screen_snapshot: window.__terminalSnapshot(marker),
        screen_snapshot_end: end,
        screen_snapshot_cols: 120,
        screen_snapshot_rows: 35,
      });
      const originalWrite = view.term.write.bind(view.term);
      let delayedMarker = '';
      view.term.write = (data, callback) => originalWrite(data, () => {
        const delay = String(data || '').includes(delayedMarker) ? 140 : 0;
        setTimeout(() => callback?.(), delay);
      });

      const conflictSeq = Number(view.deltaAcceptedSeq) + 1;
      const conflictEnd = Number(view.deltaAcceptedEnd);
      delayedMarker = 'CONFLICT_FRAME_OLD';
      const oldConflictPromise = queueTerminalScreenFrame(
        view,
        frame(conflictSeq, conflictEnd, 'CONFLICT_FRAME_OLD'),
        { authoritative: true },
      );
      const conflictStarted = await waitFor(() => Boolean(view.frameWriting));
      const newConflictPromise = queueTerminalScreenFrame(
        view,
        frame(conflictSeq, conflictEnd, 'CONFLICT_FRAME_NEW'),
        { authoritative: true },
      );
      const conflictResults = await Promise.all([oldConflictPromise, newConflictPromise]);
      const conflictSettled = await waitFor(() => (
        !view.frameWriting
        && !view.pendingFrame
        && terminalBufferVisibleText(view).includes('CONFLICT_FRAME_NEW')
      ));
      const conflictText = terminalBufferVisibleText(view);

      const reattachSeq = Number(view.deltaAcceptedSeq) + 1;
      const reattachEnd = Number(view.deltaAcceptedEnd);
      delayedMarker = 'REATTACH_FRAME_OLD';
      const oldReattachPromise = queueTerminalScreenFrame(
        view,
        frame(reattachSeq, reattachEnd, 'REATTACH_FRAME_OLD'),
        { authoritative: true },
      );
      const reattachWriteStarted = await waitFor(() => Boolean(view.frameWriting));
      const requestsBeforeReattach = requestCount();
      window.__terminalReattachScenario = {
        seq: reattachSeq,
        end: reattachEnd,
        delay: 20,
        text: 'REATTACH_FRAME_AUTHORITATIVE：新 attach 接管旧 frame 写入',
      };
      attachTerminal(true);
      const oldReattachResult = await oldReattachPromise;
      const reattachSettled = await waitFor(() => (
        !view.attaching
        && view.hydrated
        && !view.replaying
        && !view.frameWriting
        && !view.pendingFrame
        && terminalBufferVisibleText(view).includes('REATTACH_FRAME_AUTHORITATIVE：新 attach 接管旧 frame 写入')
      ));
      await new Promise(resolve => setTimeout(resolve, 180));
      const reattachText = terminalBufferVisibleText(view);
      const reattachRequestDelta = requestCount() - requestsBeforeReattach;
      view.term.write = originalWrite;

      return {
        conflictStarted,
        conflictResults,
        conflictSettled,
        conflictOldAbsent: !conflictText.includes('CONFLICT_FRAME_OLD'),
        reattachWriteStarted,
        oldReattachResult,
        reattachSettled,
        reattachOldAbsent: !reattachText.includes('REATTACH_FRAME_OLD'),
        reattachRequestDelta,
      };
    });
    assert(
      frameRaceAudit.conflictStarted
        && frameRaceAudit.conflictResults.every(Boolean)
        && frameRaceAudit.conflictSettled
        && frameRaceAudit.conflictOldAbsent
        && frameRaceAudit.reattachWriteStarted
        && frameRaceAudit.oldReattachResult === false
        && frameRaceAudit.reattachSettled
        && frameRaceAudit.reattachOldAbsent
        && frameRaceAudit.reattachRequestDelta === 0,
      'conflicting frames or an in-flight frame survived authoritative reattach',
      frameRaceAudit,
    );

    const unsafeSnapshotFallback = await page.evaluate(async () => {
      const waitFor = async predicate => {
        const deadline = performance.now() + 3000;
        while (performance.now() < deadline) {
          if (predicate()) return true;
          await new Promise(resolve => setTimeout(resolve, 20));
        }
        return false;
      };
      const requestsBefore = window.__terminalLayoutSocketEvents
        .filter(event => event.name === 'terminal_screen_request').length;
      const previous = activeTerminalView();
      const unsafeBuffer = 'UNSAFE_FALLBACK_BUFFER：参考：中文连续显示';
      const unsafeEnd = [...unsafeBuffer].length;
      previous.term.reset();
      await new Promise(resolve => {
        previous.term.write(window.__terminalSnapshot('CACHED_OLD_STREAM_MUST_NOT_SURVIVE'), resolve);
      });
      previous.hydrated = true;
      previous.replaying = false;
      previous.frameMode = false;
      previous.frameStreamId = 'cached-old-stream';
      previous.frameStreamEpoch = 9000;
      previous.renderedEnd = unsafeEnd;
      previous.replayStart = 0;
      previous.replayEnd = unsafeEnd;
      previous.replayUnits = unsafeEnd;
      previous.replayBuffer = 'CACHED_OLD_STREAM_MUST_NOT_SURVIVE';
      window.__queueTerminalScreenResponse({
        ok: true,
        screen_frame_supported: true,
        screen_snapshot_delta_safe: true,
        workspace_id: previous.workspaceId,
        terminal_id: previous.terminalId,
        stream_id: 'unsafe-stream-a',
        stream_epoch: 2000,
        frame_seq: 2,
        frame_end: unsafeEnd,
        history_start: 0,
        history_end: unsafeEnd,
        screen_snapshot: window.__terminalSnapshot('UNSAFE_RECOVERY_AUTHORITATIVE：安全帧'),
        screen_snapshot_end: unsafeEnd,
        screen_snapshot_cols: 120,
        screen_snapshot_rows: 35,
      }, 80);
      window.__terminalUnsafeSnapshotScenario = true;
      attachTerminal(true);
      const restored = await waitFor(() => {
        const view = activeTerminalView();
        const text = terminalBufferVisibleText(view);
        return view?.hydrated
          && !view.replaying
          && view.frameMode
          && !view.deltaPaused
          && text.includes('UNSAFE_RECOVERY_AUTHORITATIVE：安全帧');
      });
      const view = activeTerminalView();
      const text = terminalBufferVisibleText(view);
      const requestsAfter = window.__terminalLayoutSocketEvents
        .filter(event => event.name === 'terminal_screen_request').length;
      return {
        restored,
        frameMode: view?.frameMode,
        deltaPaused: view?.deltaPaused,
        streamId: view?.frameStreamId,
        text,
        requestsBefore,
        requestsAfter,
      };
    });
    assert(
      unsafeSnapshotFallback.restored
        && unsafeSnapshotFallback.frameMode
        && !unsafeSnapshotFallback.deltaPaused
        && unsafeSnapshotFallback.streamId === 'unsafe-stream-a'
        && unsafeSnapshotFallback.text.includes('UNSAFE_RECOVERY_AUTHORITATIVE：安全帧')
        && !unsafeSnapshotFallback.text.includes('CACHED_OLD_STREAM_MUST_NOT_SURVIVE')
        && !unsafeSnapshotFallback.text.includes('UNSAFE_FALLBACK_BUFFER：参考：中文连续显示')
        && !unsafeSnapshotFallback.text.includes('UNSAFE_SNAPSHOT_MUST_NOT_APPEAR')
        && unsafeSnapshotFallback.requestsAfter === unsafeSnapshotFallback.requestsBefore + 1,
      'an unsafe attach frame replayed raw/stale content instead of waiting for a safe authoritative frame',
      unsafeSnapshotFallback,
    );

    assert(pageErrors.length === 0, 'browser errors were reported', pageErrors);
    console.log('PASS: terminal layout stays synchronized across narrow/wide and A/B/A switches');
    for (const report of reports) {
      const kinds = [...new Set(report.events.map(event => event.name))].join(',');
      console.log(
        `  ${report.label}: ${report.term.cols}x${report.term.rows}`
        + ` (FitAddon ${report.proposed.cols}x${report.proposed.rows}; events ${kinds || 'none'})`,
      );
    }
  } finally {
    if (browser) await browser.close();
    await closeServer(server);
    rmSync(tempRoot, { recursive: true, force: true });
  }
}

run().catch(error => {
  console.error(error?.stack || String(error));
  process.exitCode = 1;
});
