import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const source = readFileSync(join(root, 'frontend', 'assets', 'app-history.js'), 'utf8');
const terminalSource = readFileSync(join(root, 'frontend', 'assets', 'app-terminal.js'), 'utf8');
const workspacesSource = readFileSync(join(root, 'frontend', 'assets', 'app-workspaces.js'), 'utf8');
const list = { innerHTML: '' };
const requests = [];
const commands = [];
let focused = false;
let reconcileQueued = 0;

const context = vm.createContext({
  console,
  Date,
  URLSearchParams,
  setTimeout,
  clearTimeout,
  activeWorkspaceId: 'alpha',
  activeTerminalId: 'main',
  DEFAULT_TERMINAL: 'main',
  termConnected: true,
  termSocket: { connected: true },
  term: { focus: () => { focused = true; } },
  codexShowArchived: false,
  codexHistoryItems: [],
  codexHistoryById: new Map(),
  document: { getElementById: id => (id === 'codexHistoryList' ? list : null) },
  syncArchivedHistoryToggle: () => {},
  escapeHtml: value => String(value),
  toast: () => {},
  showTerminalArea: () => {},
  initTerminal: () => {},
  defaultTerminalSessionName: () => 'term-2',
  publishTerminalSnapshot: async () => true,
  activeTerminalView: () => ({}),
  mergeWorkspaceSummary: () => {},
  upsertTerminalSummary: () => {},
  setActiveTerminal: terminal => { context.activeTerminalId = terminal; },
  renderWorkspaceSwitch: () => {},
  renderTerminalTabs: () => {},
  attachTerminal: () => { context.termConnected = true; },
  connectTerminal: () => { context.termConnected = true; },
  codexCommandForActiveWorkspace: () => 'codex --yolo',
  sendTerminalText: command => commands.push(command),
  queueTerminalHistoryResumeReconcile: () => { reconcileQueued += 1; },
  backendApi: {
    conversations: {
      list: async () => ({ conversations: [] }),
      update: async () => ({ ok: true }),
    },
    terminals: {
      resume: async payload => {
        requests.push({ operation: 'terminals.resume', payload });
        const resumeRequests = requests.length;
        return {
          ok: true,
          created: resumeRequests === 1,
          reused: resumeRequests > 1,
          resume_started: resumeRequests === 1,
          workspace: { id: 'alpha' },
          terminal: {
            id: 'term-2',
            name: 'term-2',
            workspace_id: 'alpha',
            thread_id: 'thread-123',
          },
        };
      },
    },
  },
});

vm.runInContext(source, context, { filename: 'app-history.js' });

context.renderCodexHistory([{
  threadId: 'thread-123',
  title: 'Recent conversation',
  rawTitle: 'Recent conversation',
  customTitle: null,
  timestamp: 1700000000,
  important: false,
  archived: false,
}]);
assert.equal(list.innerHTML.includes('绑定'), false);
assert.equal(list.innerHTML.includes('未绑定'), false);

context.renderCodexHistory([{
  threadId: 'thread-child',
  forkedFromId: 'thread-parent',
  title: 'Forked conversation',
  timestamp: 1700000000,
  important: false,
  archived: false,
}]);
assert.equal(list.innerHTML.includes('ID thread-c'), true);
assert.equal(list.innerHTML.includes('继承自 thread-p'), true);

context.codexHistoryById.set('thread-123', {
  threadId: 'thread-123',
  rawTitle: 'Recent conversation',
  customTitle: null,
});
context.termConnected = false;
await context.resumeCodexThread('thread-123');

assert.equal(requests.length, 1);
assert.equal(requests[0].operation, 'terminals.resume');
assert.deepEqual(JSON.parse(JSON.stringify(requests[0].payload)), {
  workspace_id: 'alpha',
  name: 'Rece',
  thread_id: 'thread-123',
});
assert.deepEqual(commands, []);
assert.equal(reconcileQueued, 1);
assert.equal(focused, true);

await context.resumeCodexThread('thread-123');
assert.equal(requests.length, 2);
assert.equal(requests[1].operation, 'terminals.resume');
assert.deepEqual(commands, []);
assert.equal(reconcileQueued, 1);

function functionSource(sourceText, name) {
  const signatures = [`async function ${name}(`, `function ${name}(`];
  const start = signatures.map(signature => sourceText.indexOf(signature))
    .find(index => index >= 0);
  assert.notEqual(start, undefined, `missing function ${name}`);
  const bodyStart = sourceText.indexOf('{', start);
  let depth = 0;
  for (let index = bodyStart; index < sourceText.length; index += 1) {
    if (sourceText[index] === '{') depth += 1;
    if (sourceText[index] === '}') depth -= 1;
    if (depth === 0) return sourceText.slice(start, index + 1);
  }
  throw new Error(`unterminated function ${name}`);
}

const mappedTerminals = [
  { id: 'main', name: 'Main shell', thread_id: 'thread-shared', thread_title: 'Stale title' },
  { id: 'plain', name: 'Plain shell' },
];
const secondWorkspaceTerminals = [
  { id: 'review', name: 'Review shell', thread_id: 'thread-shared', thread_title: 'Stale title' },
];
const metadataUpdates = [];
const mappingToasts = [];
let historyReloads = 0;
const mappingContext = vm.createContext({
  console,
  DEFAULT_TERMINAL: 'main',
  activeWorkspaceId: 'alpha',
  codexHistoryById: new Map([['thread-shared', {
    threadId: 'thread-shared',
    title: 'Canonical conversation title',
  }]]),
  workspaceList: [
    { id: 'alpha', terminals: mappedTerminals },
    { id: 'beta', terminals: secondWorkspaceTerminals },
  ],
  activeWorkspaceTerminals: () => mappedTerminals,
  window: {
    prompt: (message, value) => {
      assert.equal(message, '修改对话名称');
      assert.equal(value, 'Canonical conversation title');
      return 'Shared renamed title';
    },
  },
  updateCodexHistoryMetadata: async (threadId, updates) => {
    metadataUpdates.push({ threadId, updates });
    return { ok: true };
  },
  renderWorkspaceSwitch: () => {},
  renderTerminalTabs: () => {},
  loadCodexHistory: async () => { historyReloads += 1; },
  toast: (message, type) => mappingToasts.push({ message, type }),
  backendApi: {
    terminals: {
      rename: async () => { throw new Error('mapped rename must not call terminal rename API'); },
    },
  },
});
vm.runInContext([
  functionSource(workspacesSource, 'terminalLabel'),
  functionSource(workspacesSource, 'renameTerminalSession'),
].join('\n'), mappingContext, { filename: 'app-workspaces-mapping.js' });

assert.equal(mappingContext.terminalLabel(mappedTerminals[0]), 'Canonical conversation title');
assert.equal(mappingContext.terminalLabel(mappedTerminals[1]), 'Plain shell');
await mappingContext.renameTerminalSession('main');
assert.equal(metadataUpdates.length, 1);
assert.equal(metadataUpdates[0].threadId, 'thread-shared');
assert.equal(metadataUpdates[0].updates.title, 'Shared renamed title');
assert.equal(mappedTerminals[0].thread_title, 'Shared renamed title');
assert.equal(secondWorkspaceTerminals[0].thread_title, 'Shared renamed title');
assert.equal(mappingContext.codexHistoryById.get('thread-shared').title, 'Shared renamed title');
assert.equal(historyReloads, 1);
assert.deepEqual(mappingToasts, [{ message: '对话名称已更新', type: 'success' }]);

const displayContext = vm.createContext({
  console,
  setTimeout,
  clearTimeout,
  window: { addEventListener: () => {} },
});
vm.runInContext(terminalSource, displayContext, { filename: 'app-terminal.js' });
const longHistory = Array.from({ length: 2000 }, (_, index) => `history-${index}`).join('\r\n');
const formatted = displayContext.formatTerminalSnapshotForDisplay(
  {},
  `\x1b[2J\x1b[H\x1b(B\x1b)0\x0f\x1b[0m› restored user input\r\n${longHistory}\r\nHISTORY_FINAL_TAIL`
);
assert.equal(formatted.includes('\x1b[48;5;238m'), true);
assert.equal(formatted.includes('\x1b[0m\x1b[48;5;238m› restored user input'), true);
assert.equal(formatted.indexOf('\x1b[48;5;238m') > formatted.indexOf('\x1b[2J'), true);
assert.equal(formatted.endsWith('HISTORY_FINAL_TAIL'), true);

console.log('PASS: History resume is idempotent and terminal titles follow canonical thread IDs');
