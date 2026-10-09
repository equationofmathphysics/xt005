(() => {
  const PROMPT_PREFIX = '› ';
  const PROMPT_TEXT_LIMIT = 4000;
  const PROMPT_ARIA_LIMIT = 180;

  const navigatorState = {
    initialized: false,
    frame: 0,
    scanRequested: false,
    forceScanRequested: false,
    pendingView: null,
    renderedViewKey: '',
    renderedSignature: '',
    entries: [],
    activeIndex: -1,
    hoverIndex: -1,
  };

  function promptNavigatorElements() {
    return {
      nav: document.getElementById('terminalPromptNavigator'),
      rail: document.getElementById('terminalPromptRail'),
      list: document.getElementById('terminalPromptList'),
    };
  }

  function terminalBufferLine(buffer, index) {
    if (!buffer || index < 0 || index >= Number(buffer.length || 0)) return null;
    try {
      return buffer.getLine(index) || null;
    } catch(e) {
      return null;
    }
  }

  function terminalBufferLineText(line, trimRight = true) {
    if (!line) return '';
    try {
      return line.translateToString(trimRight) || '';
    } catch(e) {
      return '';
    }
  }

  function terminalPromptBufferFingerprint(buffer) {
    const length = Number(buffer?.length || 0);
    if (!length) return '';
    const indexes = [0, 1, 2].filter(index => index < length);
    return indexes.map(index => {
      const line = terminalBufferLine(buffer, index);
      return (line?.isWrapped ? '1:' : '0:') + terminalBufferLineText(line).slice(0, 80);
    }).join('|');
  }

  function terminalPromptTextAt(buffer, startLine) {
    const firstLine = terminalBufferLine(buffer, startLine);
    if (!firstLine || firstLine.isWrapped) return null;
    const firstText = terminalBufferLineText(firstLine);
    if (!firstText.startsWith(PROMPT_PREFIX)) return null;

    const parts = [];
    let lineIndex = startLine;
    let endLine = startLine;
    while (lineIndex < Number(buffer.length || 0)) {
      const line = terminalBufferLine(buffer, lineIndex);
      if (!line) break;
      const nextLine = terminalBufferLine(buffer, lineIndex + 1);
      const continues = Boolean(nextLine?.isWrapped);
      let value = terminalBufferLineText(line, !continues);
      if (lineIndex === startLine) value = value.slice(PROMPT_PREFIX.length);
      parts.push(value);
      endLine = lineIndex;
      if (!continues) break;
      lineIndex += 1;
    }

    const text = parts.join('').replace(/\s+/g, ' ').trim().slice(0, PROMPT_TEXT_LIMIT);
    if (!text) return null;
    return { line: startLine, endLine, text };
  }

  function terminalPromptViewCache(view) {
    if (!view.promptNavigatorState) {
      view.promptNavigatorState = {
        buffer: null,
        bufferLength: 0,
        bufferFingerprint: '',
        entries: [],
      };
    }
    return view.promptNavigatorState;
  }

  function scanTerminalPromptEntries(view, forceFull = false) {
    const buffer = view?.term?.buffer?.active;
    if (!buffer) return [];
    const cache = terminalPromptViewCache(view);
    const length = Number(buffer.length || 0);
    const fingerprint = terminalPromptBufferFingerprint(buffer);
    const fullScan = Boolean(
      forceFull
      || cache.buffer !== buffer
      || length < cache.bufferLength
      || (length === cache.bufferLength && fingerprint !== cache.bufferFingerprint)
    );

    let startLine = 0;
    let entries = [];
    if (!fullScan) {
      const recentLines = Math.max(8, Number(view.term.rows || 24) + 3);
      startLine = Math.max(0, Math.min(cache.bufferLength, length) - recentLines);
      while (startLine > 0 && terminalBufferLine(buffer, startLine)?.isWrapped) {
        startLine -= 1;
      }
      entries = cache.entries.filter(entry => entry.endLine < startLine);
    }

    for (let lineIndex = startLine; lineIndex < length; lineIndex += 1) {
      const entry = terminalPromptTextAt(buffer, lineIndex);
      if (!entry) continue;
      entries.push(entry);
      lineIndex = entry.endLine;
    }

    cache.buffer = buffer;
    cache.bufferLength = length;
    cache.bufferFingerprint = fingerprint;
    cache.entries = entries;
    return entries;
  }

  function promptEntriesSignature(view, entries) {
    return String(view?.key || '') + ':' + JSON.stringify(
      entries.map(entry => [entry.line, entry.endLine, entry.text])
    );
  }

  function currentTerminalPromptIndex(view, entries) {
    if (!entries.length) return -1;
    const buffer = view?.term?.buffer?.active;
    const viewportY = Math.max(0, Number(buffer?.viewportY || 0));
    const rows = Math.max(1, Number(view?.term?.rows || 1));
    const anchorLine = viewportY + Math.floor(rows * 0.25);
    let current = 0;
    for (let index = 0; index < entries.length; index += 1) {
      if (entries[index].line > anchorLine) break;
      current = index;
    }
    return current;
  }

  function promptButtonLabel(entry, index) {
    const text = entry.text.length > PROMPT_ARIA_LIMIT
      ? entry.text.slice(0, PROMPT_ARIA_LIMIT) + '...'
      : entry.text;
    return '跳转到用户输入 ' + (index + 1) + '：' + text;
  }

  function createPromptMarker(entry, index) {
    const button = document.createElement('button');
    button.className = 'terminal-prompt-marker';
    button.type = 'button';
    button.dataset.promptIndex = String(index);
    button.setAttribute('role', 'listitem');
    button.setAttribute('aria-label', promptButtonLabel(entry, index));
    const line = document.createElement('span');
    line.className = 'terminal-prompt-marker-line';
    line.setAttribute('aria-hidden', 'true');
    button.appendChild(line);
    return button;
  }

  function createPromptListItem(entry, index) {
    const button = document.createElement('button');
    button.className = 'terminal-prompt-list-item';
    button.type = 'button';
    button.dataset.promptIndex = String(index);
    button.setAttribute('role', 'listitem');
    button.setAttribute('aria-label', promptButtonLabel(entry, index));
    const text = document.createElement('span');
    text.className = 'terminal-prompt-list-text';
    text.textContent = entry.text;
    button.appendChild(text);
    return button;
  }

  function setPromptNavigatorVisible(visible) {
    const { nav } = promptNavigatorElements();
    if (!nav) return;
    nav.hidden = !visible;
    nav.setAttribute('aria-hidden', visible ? 'false' : 'true');
    if (!visible) nav.classList.remove('expanded');
  }

  function renderTerminalPromptEntries(view, entries) {
    const { rail, list } = promptNavigatorElements();
    if (!rail || !list) return;
    const railFragment = document.createDocumentFragment();
    const listFragment = document.createDocumentFragment();
    entries.forEach((entry, index) => {
      railFragment.appendChild(createPromptMarker(entry, index));
      listFragment.appendChild(createPromptListItem(entry, index));
    });
    rail.replaceChildren(railFragment);
    list.replaceChildren(listFragment);
    navigatorState.renderedViewKey = String(view?.key || '');
    navigatorState.renderedSignature = promptEntriesSignature(view, entries);
    navigatorState.entries = entries;
    navigatorState.activeIndex = -1;
    navigatorState.hoverIndex = -1;
    setPromptNavigatorVisible(entries.length > 0);
  }

  function syncTerminalPromptSelection(view, options = {}) {
    const { nav, rail, list } = promptNavigatorElements();
    if (!nav || nav.hidden || !rail || !list) return;
    const activeIndex = currentTerminalPromptIndex(view, navigatorState.entries);
    const selectionChanged = activeIndex !== navigatorState.activeIndex;
    navigatorState.activeIndex = activeIndex;

    for (const element of nav.querySelectorAll('[data-prompt-index]')) {
      const index = Number(element.dataset.promptIndex);
      element.classList.toggle('active', index === activeIndex);
      element.classList.toggle('hovered', index === navigatorState.hoverIndex);
      if (index === activeIndex) element.setAttribute('aria-current', 'location');
      else element.removeAttribute('aria-current');
    }

    if (nav.classList.contains('expanded') && (selectionChanged || options.ensureVisible)) {
      const revealIndex = navigatorState.hoverIndex >= 0
        ? navigatorState.hoverIndex
        : activeIndex;
      const selector = `.terminal-prompt-list-item[data-prompt-index="${revealIndex}"]`;
      const revealItem = list.querySelector(selector);
      try { revealItem?.scrollIntoView({ block: 'nearest' }); } catch(e) {}
    }
    if (selectionChanged || options.ensureVisible) {
      const marker = rail.querySelector(`.terminal-prompt-marker[data-prompt-index="${activeIndex}"]`);
      try { marker?.scrollIntoView({ block: 'nearest' }); } catch(e) {}
    }
  }

  function activePromptNavigatorView() {
    if (typeof window.activeTerminalView !== 'function') return null;
    return window.activeTerminalView();
  }

  function promptNavigatorWheelDelta(event, scroller) {
    const delta = Number(event?.deltaY || 0);
    if (!Number.isFinite(delta) || !delta) return 0;
    if (Number(event.deltaMode) === 1) {
      const item = scroller.querySelector('[data-prompt-index]');
      const itemHeight = Number(item?.getBoundingClientRect?.().height || 20);
      return delta * Math.max(1, itemHeight);
    }
    if (Number(event.deltaMode) === 2) {
      return delta * Math.max(1, Number(scroller.clientHeight || 1));
    }
    return delta;
  }

  function syncPromptNavigatorFromTerminalWheel(event) {
    const view = activePromptNavigatorView();
    const { nav, rail, list } = promptNavigatorElements();
    if (!view || !nav || nav.hidden) return;
    for (const scroller of [rail, list]) {
      if (!scroller || scroller.scrollHeight <= scroller.clientHeight) continue;
      scroller.scrollTop += promptNavigatorWheelDelta(event, scroller);
    }
  }

  function refreshTerminalPromptNavigator(view, options = {}) {
    const activeView = activePromptNavigatorView();
    if (!view || view !== activeView || view.disposed || !view.term) {
      if (!activeView) setPromptNavigatorVisible(false);
      return;
    }
    const cache = terminalPromptViewCache(view);
    const entries = options.scan === false
      ? cache.entries
      : scanTerminalPromptEntries(view, options.force === true);
    const signature = promptEntriesSignature(view, entries);
    if (
      signature !== navigatorState.renderedSignature
      || String(view.key || '') !== navigatorState.renderedViewKey
    ) {
      renderTerminalPromptEntries(view, entries);
    } else {
      navigatorState.entries = entries;
      setPromptNavigatorVisible(entries.length > 0);
    }
    syncTerminalPromptSelection(view, options);
  }

  function runScheduledPromptNavigatorRefresh() {
    navigatorState.frame = 0;
    const activeView = activePromptNavigatorView();
    const view = navigatorState.pendingView === activeView
      ? navigatorState.pendingView
      : activeView;
    const options = {
      scan: navigatorState.scanRequested,
      force: navigatorState.forceScanRequested,
    };
    navigatorState.pendingView = null;
    navigatorState.scanRequested = false;
    navigatorState.forceScanRequested = false;
    refreshTerminalPromptNavigator(view, options);
  }

  function scheduleTerminalPromptNavigatorRefresh(view = activePromptNavigatorView(), options = {}) {
    if (!navigatorState.initialized) initTerminalPromptNavigator();
    navigatorState.pendingView = view;
    navigatorState.scanRequested = navigatorState.scanRequested || options.scan !== false;
    navigatorState.forceScanRequested = navigatorState.forceScanRequested || options.force === true;
    if (navigatorState.frame) return;
    const schedule = typeof requestAnimationFrame === 'function'
      ? requestAnimationFrame
      : callback => setTimeout(callback, 0);
    navigatorState.frame = schedule(runScheduledPromptNavigatorRefresh);
  }

  function resetTerminalPromptNavigator(view = activePromptNavigatorView()) {
    if (view) view.promptNavigatorState = null;
    if (view && view !== activePromptNavigatorView()) return;
    navigatorState.renderedViewKey = '';
    navigatorState.renderedSignature = '';
    navigatorState.entries = [];
    navigatorState.activeIndex = -1;
    navigatorState.hoverIndex = -1;
    const { rail, list } = promptNavigatorElements();
    rail?.replaceChildren();
    list?.replaceChildren();
    setPromptNavigatorVisible(false);
  }

  function hideTerminalPromptNavigator() {
    navigatorState.renderedViewKey = '';
    navigatorState.renderedSignature = '';
    navigatorState.entries = [];
    navigatorState.activeIndex = -1;
    navigatorState.hoverIndex = -1;
    setPromptNavigatorVisible(false);
  }

  function setPromptNavigatorHover(index) {
    navigatorState.hoverIndex = Number.isInteger(index) ? index : -1;
    const view = activePromptNavigatorView();
    if (view) syncTerminalPromptSelection(view, { ensureVisible: true });
  }

  function jumpToTerminalPrompt(index) {
    const view = activePromptNavigatorView();
    const entry = navigatorState.entries[index];
    if (!view?.term || !entry) return false;
    if (typeof window.setTerminalFollowOutput === 'function') {
      window.setTerminalFollowOutput(view, false);
    } else {
      view.followOutput = false;
    }
    const contextLines = Math.min(2, Math.max(0, Math.floor(Number(view.term.rows || 1) * 0.1)));
    try { view.term.scrollToLine(Math.max(0, entry.line - contextLines)); } catch(e) { return false; }
    try { view.term.focus(); } catch(e) {}
    setTimeout(() => scheduleTerminalPromptNavigatorRefresh(view, { scan: false }), 0);
    return true;
  }

  function promptIndexFromEvent(event) {
    const button = event.target?.closest?.('[data-prompt-index]');
    if (!button) return -1;
    const index = Number(button.dataset.promptIndex);
    return Number.isInteger(index) ? index : -1;
  }

  function initTerminalPromptNavigator() {
    if (navigatorState.initialized) return;
    const { nav } = promptNavigatorElements();
    if (!nav) return;
    navigatorState.initialized = true;

    document.getElementById('terminalContainer')?.addEventListener(
      'wheel',
      syncPromptNavigatorFromTerminalWheel,
      { capture: true, passive: true },
    );

    nav.addEventListener('pointerenter', () => {
      nav.classList.add('expanded');
      const view = activePromptNavigatorView();
      if (view) syncTerminalPromptSelection(view, { ensureVisible: true });
    });
    nav.addEventListener('pointerleave', () => {
      nav.classList.remove('expanded');
      setPromptNavigatorHover(-1);
    });
    nav.addEventListener('pointerover', event => {
      const index = promptIndexFromEvent(event);
      if (index >= 0) setPromptNavigatorHover(index);
    });
    nav.addEventListener('focusin', event => {
      nav.classList.add('expanded');
      const index = promptIndexFromEvent(event);
      if (index >= 0) setPromptNavigatorHover(index);
    });
    nav.addEventListener('focusout', () => {
      setTimeout(() => {
        if (nav.contains(document.activeElement)) return;
        nav.classList.remove('expanded');
        setPromptNavigatorHover(-1);
      }, 0);
    });
    nav.addEventListener('click', event => {
      const index = promptIndexFromEvent(event);
      if (index < 0) return;
      event.preventDefault();
      jumpToTerminalPrompt(index);
    });
  }

  window.initTerminalPromptNavigator = initTerminalPromptNavigator;
  window.scheduleTerminalPromptNavigatorRefresh = scheduleTerminalPromptNavigatorRefresh;
  window.resetTerminalPromptNavigator = resetTerminalPromptNavigator;
  window.hideTerminalPromptNavigator = hideTerminalPromptNavigator;
  window.jumpToTerminalPrompt = jumpToTerminalPrompt;
})();
