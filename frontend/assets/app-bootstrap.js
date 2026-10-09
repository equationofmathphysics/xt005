// ===================== 启动 =====================

(async () => {
  restoreCodexSidebarState();
  await loadWorkspaces();
  initTerminal();
  if (typeof initTerminalPromptNavigator === 'function') initTerminalPromptNavigator();
  loadCodexHistory();
  codexHistoryLoaded = true;
  startStatsRefresh();
})();
