const API_BASE_URL = '';

const HTTP_API_PATHS = Object.freeze({
  health: '/api/check',
  workspacePin: '/api/workspaces/pin',
  terminalAgent: '/api/workspace-terminals/agent',
  terminalInterrupt: '/api/workspace-terminals/interrupt',
  conversationLocation: '/api/codex-history/{thread_id}/location',
  conversationRead: '/api/codex-history/{thread_id}/read',
  directories: '/api/fs-dirs',
  systemStats: '/api/system-stats',
  workspaces: '/api/workspaces',
  workspaceRegister: '/api/workspaces/register',
  workspaceClose: '/api/workspaces/close',
  workspaceReorder: '/api/workspaces/reorder',
  workspaceAgentRestart: '/api/workspace-agent/restart',
  terminals: '/api/workspace-terminals',
  terminalCreate: '/api/workspace-terminals/create',
  terminalResume: '/api/workspace-terminals/resume',
  terminalClose: '/api/workspace-terminals/close',
  terminalRename: '/api/workspace-terminals/rename',
  terminalBuffer: '/api/terminal-buffer',
  terminalScreen: '/api/terminal-screen',
  conversations: '/api/codex-history',
  conversation: '/api/codex-history/{thread_id}',
  files: '/api/workspace-files',
  fileRead: '/api/workspace-files/read',
  fileWrite: '/api/workspace-files/write',
  fileCreate: '/api/workspace-files/create',
  fileUpload: '/api/workspace-files/upload',
  fileDownload: '/api/workspace-files/download',
  fileOpenFolder: '/api/workspace-files/open-folder',
  observations: '/api/workspace-observations',
  observationsRead: '/api/workspace-observations/read',
  hookReport: '/api/hooks/report',
});

class BackendApiError extends Error {
  constructor(message, status, payload = null) {
    super(message);
    this.name = 'BackendApiError';
    this.status = status;
    this.payload = payload;
  }
}

function apiPath(path, query = null) {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    params.set(key, String(value));
  }
  const encoded = params.toString();
  return encoded ? path + '?' + encoded : path;
}

function apiResourcePath(path, params) {
  return Object.entries(params).reduce(
    (resolved, [key, value]) => resolved.replace(
      '{' + key + '}',
      encodeURIComponent(String(value)),
    ),
    path,
  );
}

async function apiResponse(path, opts = {}) {
  const timeout = Number(opts.timeout || 15000);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const { timeout: _timeout, headers: optHeaders, signal: externalSignal, ...fetchOpts } = opts;
  const headers = { ...(optHeaders || {}) };
  if (fetchOpts.body && !(fetchOpts.body instanceof FormData) && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }
  const abortFromExternal = () => controller.abort();
  if (externalSignal?.aborted) controller.abort();
  else externalSignal?.addEventListener('abort', abortFromExternal, { once: true });

  try {
    const response = await fetch(API_BASE_URL + path, {
      cache: 'no-store',
      ...fetchOpts,
      headers,
      signal: controller.signal,
    });
    if (response.ok) return response;
    const payload = await response.json().catch(() => null);
    throw new BackendApiError(
      payload?.error || 'HTTP ' + response.status,
      response.status,
      payload,
    );
  } finally {
    clearTimeout(timer);
    externalSignal?.removeEventListener('abort', abortFromExternal);
  }
}

async function requestJson(path, opts = {}) {
  const response = await apiResponse(path, opts);
  return response.json();
}

async function downloadFile(path, filename) {
  const response = await apiResponse(path);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement('a');
    link.href = url;
    link.download = filename || '';
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}

const backendApi = Object.freeze({
  system: Object.freeze({
    health: () => requestJson(HTTP_API_PATHS.health),
    directories: path => requestJson(apiPath(HTTP_API_PATHS.directories, { path })),
    stats: () => requestJson(HTTP_API_PATHS.systemStats),
  }),
  workspaces: Object.freeze({
    list: () => requestJson(HTTP_API_PATHS.workspaces),
    register: payload => requestJson(HTTP_API_PATHS.workspaceRegister, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
    close: workspaceId => requestJson(HTTP_API_PATHS.workspaceClose, {
      method: 'POST',
      body: JSON.stringify({ workspace_id: workspaceId }),
    }),
    reorder: order => requestJson(HTTP_API_PATHS.workspaceReorder, {
      method: 'POST',
      body: JSON.stringify({ order }),
    }),
    restartAgent: (workspaceId, threadId = '') => requestJson(
      HTTP_API_PATHS.workspaceAgentRestart,
      {
        method: 'POST',
        body: JSON.stringify({ workspace_id: workspaceId, thread_id: threadId }),
      },
    ),
  }),
  terminals: {
    list: workspaceId => requestJson(apiPath(HTTP_API_PATHS.terminals, {
      workspace_id: workspaceId,
    })),
    create: payload => requestJson(HTTP_API_PATHS.terminalCreate, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
    resume: payload => requestJson(HTTP_API_PATHS.terminalResume, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
    close: (workspaceId, terminalId) => requestJson(HTTP_API_PATHS.terminalClose, {
      method: 'POST',
      body: JSON.stringify({ workspace_id: workspaceId, terminal_id: terminalId }),
    }),
    rename: (workspaceId, terminalId, name) => requestJson(HTTP_API_PATHS.terminalRename, {
      method: 'POST',
      body: JSON.stringify({ workspace_id: workspaceId, terminal_id: terminalId, name }),
    }),
    buffer: query => requestJson(apiPath(HTTP_API_PATHS.terminalBuffer, query)),
    screen: (query, opts = {}) => requestJson(apiPath(HTTP_API_PATHS.terminalScreen, query), opts),
  },
  conversations: Object.freeze({
    list: (workspaceId, includeArchived = false, offset = 0) => requestJson(apiPath(
      HTTP_API_PATHS.conversations,
      {
        workspace_id: workspaceId,
        include_archived: includeArchived ? 1 : undefined,
        offset,
      },
    )),
    update: (threadId, updates) => requestJson(
      apiResourcePath(HTTP_API_PATHS.conversation, { thread_id: threadId }),
      {
        method: 'PATCH',
        body: JSON.stringify(updates),
      },
    ),
  }),
  files: Object.freeze({
    list: (workspaceId, dir = '') => requestJson(apiPath(HTTP_API_PATHS.files, {
      workspace_id: workspaceId,
      dir,
    })),
    read: (workspaceId, path) => requestJson(apiPath(HTTP_API_PATHS.fileRead, {
      workspace_id: workspaceId,
      path,
    })),
    write: (workspaceId, path, content) => requestJson(HTTP_API_PATHS.fileWrite, {
      method: 'POST',
      body: JSON.stringify({ workspace_id: workspaceId, path, content }),
    }),
    create: (workspaceId, path, isDir = false) => requestJson(HTTP_API_PATHS.fileCreate, {
      method: 'POST',
      body: JSON.stringify({ workspace_id: workspaceId, path, is_dir: isDir }),
    }),
    upload: (workspaceId, dir, file) => {
      const form = new FormData();
      form.append('workspace_id', workspaceId);
      form.append('dir', dir || '');
      form.append('file', file);
      return requestJson(HTTP_API_PATHS.fileUpload, { method: 'POST', body: form });
    },
    download: (workspaceId, path, filename) => downloadFile(
      apiPath(HTTP_API_PATHS.fileDownload, { workspace_id: workspaceId, path }),
      filename,
    ),
    openFolder: (workspaceId, dir = '') => requestJson(HTTP_API_PATHS.fileOpenFolder, {
      method: 'POST',
      body: JSON.stringify({ workspace_id: workspaceId, dir }),
    }),
  }),
  observations: Object.freeze({
    get: workspaceId => requestJson(apiPath(HTTP_API_PATHS.observations, {
      workspace_id: workspaceId,
    })),
    markRead: (workspaceId, terminalId = '') => requestJson(HTTP_API_PATHS.observationsRead, {
      method: 'POST',
      body: JSON.stringify({ workspace_id: workspaceId, terminal_id: terminalId || undefined }),
    }),
    reportHook: payload => requestJson(HTTP_API_PATHS.hookReport, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  }),
});
