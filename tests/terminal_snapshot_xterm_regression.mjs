#!/usr/bin/env node

import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { existsSync, readdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { delimiter, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(TEST_DIR, '..');
const STRICT = process.env.TERMINAL_SNAPSHOT_REQUIRE_PLAYWRIGHT === '1'
  || process.env.TERMINAL_LAYOUT_REQUIRE_PLAYWRIGHT === '1';

function skip(message) {
  console.log(`SKIP: ${message}`);
  process.exitCode = STRICT ? 1 : 77;
}

function assert(condition, message, details = null) {
  if (condition) return;
  const suffix = details === null ? '' : `\n${JSON.stringify(details, null, 2)}`;
  throw new Error(`${message}${suffix}`);
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
      // Use only an already installed dependency; this test never downloads one.
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

const PYTHON_FIXTURE = String.raw`
import json

from codexws_server.terminal_screen import TerminalScreenModel


def visible_lines(model):
    return [line.rstrip() for line in model.screen.display]


def history_lines(model):
    return [
        "".join(char.data for _, char in sorted(line.items())).rstrip()
        for line in model.screen.history.top
    ]


source = TerminalScreenModel(32, 6, history_lines=64)
for index in range(12):
    source.feed(f"normal-{index:02d} \u53c2\u8003\r\n")
source.feed("\x1b[6;10H\x1b[34m")

normal_display = visible_lines(source)
normal_history = history_lines(source)
normal_cursor = [source.screen.cursor.x, source.screen.cursor.y]

source.feed("\x1b[?1049h\x1b[HALT-BASE \u53c2\u8003")
source.feed("\x1b[3g\x1b[10G\x1bH\x1b[20G\x1bH")
source.feed("\x1b[2;5r\x1b[?6l\x1b[?7l")
source.feed("\x1b[4;2H\x1b[31;1m\x1b7")
source.feed("\x1b[?6h\x1b[?7h\x1b[2;4H\x1b[38;2;12;34;56;1;3m\x1b[?25l")

pending_wrap = TerminalScreenModel(5, 3)
pending_wrap.feed("abcde")

charset = TerminalScreenModel(20, 3)
charset.feed("\x1b(0lq")

expanded_tabs = TerminalScreenModel(80, 3)
expanded_tabs.resize(120, 3)
expanded_tabs.feed("\x1b[1;79H")

extended_sgr = TerminalScreenModel(20, 3)
extended_sgr.feed("\x1b[2;8mS")

rich_sgr = TerminalScreenModel(20, 3)
rich_sgr.feed("\x1b[4:3;53;58;2;1;2;3mS")

isolated_alternate = TerminalScreenModel(20, 6)
isolated_alternate.feed("\x1b[2;5r\x1b[3g\x1b[11G\x1bH")
isolated_alternate.feed("\x1b[?1049h\x1b[HA\tB")

print(json.dumps({
    "cols": source.cols,
    "rows": source.rows,
    "snapshot": source.serialize(),
    "normal_display": normal_display,
    "normal_history": normal_history,
    "normal_cursor": normal_cursor,
    "alternate_display": visible_lines(source),
    "alternate_cursor": [source.screen.cursor.x, source.screen.cursor.y],
    "pending_wrap_snapshot": pending_wrap.serialize(),
    "charset_snapshot": charset.serialize(),
    "expanded_tabs_snapshot": expanded_tabs.serialize(),
    "extended_sgr_snapshot": extended_sgr.serialize(),
    "rich_sgr_snapshot": rich_sgr.serialize(),
    "isolated_alternate_snapshot": isolated_alternate.serialize(),
}, ensure_ascii=True))
`;

function buildFixture() {
  const pythonPath = [
    join(REPO_ROOT, 'src'),
    process.env.PYTHONPATH || '',
  ].filter(Boolean).join(delimiter);
  const result = spawnSync(process.env.PYTHON || 'python3', ['-c', PYTHON_FIXTURE], {
    cwd: REPO_ROOT,
    env: { ...process.env, PYTHONPATH: pythonPath },
    encoding: 'utf8',
  });
  if (result.error) throw result.error;
  assert(result.status === 0, 'Python snapshot fixture failed', {
    stdout: result.stdout,
    stderr: result.stderr,
    status: result.status,
  });
  return JSON.parse(result.stdout);
}

async function writeTerminal(page, data) {
  await page.evaluate(text => new Promise(resolve => window.term.write(text, resolve)), data);
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

  const fixture = buildFixture();
  const pageErrors = [];
  let browser;
  try {
    browser = await playwright.chromium.launch({
      executablePath,
      headless: true,
      args: ['--disable-dev-shm-usage'],
    });
    const page = await browser.newPage({ viewport: { width: 900, height: 500 } });
    page.on('pageerror', error => pageErrors.push(error?.stack || String(error)));
    page.on('console', message => {
      if (message.type() === 'error') pageErrors.push(`console: ${message.text()}`);
    });

    await page.setContent(`
      <!doctype html>
      <html>
        <head><meta charset="utf-8"></head>
        <body><div id="terminal" style="width:860px;height:420px"></div></body>
      </html>
    `);
    await page.addStyleTag({ path: join(REPO_ROOT, '.cdnlocal/xterm/5.3.0/xterm.css') });
    await page.addScriptTag({ path: join(REPO_ROOT, '.cdnlocal/xterm/5.3.0/xterm.js') });
    await page.evaluate(({ cols, rows }) => {
      window.resetTerminal = ({ cols: nextCols, rows: nextRows }) => {
        const mount = document.getElementById('terminal');
        if (!window.term) {
          window.term = new window.Terminal({
            cols: nextCols,
            rows: nextRows,
            scrollback: 256,
            allowProposedApi: true,
            convertEol: false,
          });
          window.term.open(mount);
        } else {
          window.term.reset();
          window.term.resize(nextCols, nextRows);
        }
      };
      window.readTerminalState = () => {
        const buffer = window.term.buffer.active;
        const lineText = index => buffer.getLine(index)?.translateToString(true) || '';
        const visible = [];
        for (let row = 0; row < window.term.rows; row += 1) {
          visible.push(lineText(buffer.baseY + row));
        }
        const history = [];
        for (let row = 0; row < buffer.baseY; row += 1) history.push(lineText(row));
        return {
          type: buffer.type,
          cursor: [buffer.cursorX, buffer.cursorY],
          baseY: buffer.baseY,
          visible,
          history,
        };
      };
      window.readVisibleCell = (x, y) => {
        const buffer = window.term.buffer.active;
        const cell = buffer.getLine(buffer.baseY + y)?.getCell(x);
        if (!cell) return null;
        return {
          chars: cell.getChars(),
          fg: cell.getFgColor(),
          fgMode: cell.getFgColorMode(),
          bold: Boolean(cell.isBold()),
          italic: Boolean(cell.isItalic()),
          dim: typeof cell.isDim === 'function' ? Boolean(cell.isDim()) : null,
          invisible: typeof cell.isInvisible === 'function' ? Boolean(cell.isInvisible()) : null,
          underline: typeof cell.isUnderline === 'function' ? Boolean(cell.isUnderline()) : null,
          underlineStyle: typeof cell.getUnderlineStyle === 'function' ? cell.getUnderlineStyle() : null,
          underlineColor: typeof cell.getUnderlineColor === 'function' ? cell.getUnderlineColor() : null,
          overline: typeof cell.isOverline === 'function' ? Boolean(cell.isOverline()) : null,
        };
      };
      window.resetTerminal({ cols, rows });
    }, fixture);

    await writeTerminal(page, fixture.snapshot);
    const initial = await page.evaluate(() => window.readTerminalState());
    assert(initial.type === 'alternate', 'snapshot did not activate the alternate buffer', initial);
    assert(
      JSON.stringify(initial.cursor) === JSON.stringify(fixture.alternate_cursor),
      'alternate cursor was not restored',
      { actual: initial.cursor, expected: fixture.alternate_cursor },
    );
    assert(
      JSON.stringify(initial.visible) === JSON.stringify(fixture.alternate_display),
      'alternate visible content was not restored',
      { actual: initial.visible, expected: fixture.alternate_display },
    );

    const esc = String.fromCharCode(27);
    const alternateDelta = [
      'X\tY',
      `${esc}[1;6HZ`,
      `${esc}8S`,
    ].join('');
    await writeTerminal(page, alternateDelta);

    const cells = await page.evaluate(() => ({
      current: window.readVisibleCell(3, 2),
      tabbed: window.readVisibleCell(9, 2),
      origin: window.readVisibleCell(5, 1),
      saved: window.readVisibleCell(1, 3),
    }));
    const trueColor = (12 << 16) | (34 << 8) | 56;
    assert(
      cells.current?.chars === 'X'
        && cells.current.fg === trueColor
        && cells.current.bold
        && cells.current.italic,
      'raw delta did not continue at the restored cursor with active SGR',
      cells,
    );
    assert(
      cells.tabbed?.chars === 'Y' && cells.tabbed.fg === trueColor,
      'custom tab stops were not restored before the raw delta',
      cells,
    );
    assert(
      cells.origin?.chars === 'Z' && cells.origin.fg === trueColor,
      'scroll margins/origin mode were not restored',
      cells,
    );
    assert(
      cells.saved?.chars === 'S'
        && cells.saved.fg === 1
        && cells.saved.bold
        && !cells.saved.italic,
      'saved cursor position/SGR were not restored before the raw delta',
      cells,
    );

    await writeTerminal(page, `${esc}[6;25H${esc}8T`);
    const savedAgain = await page.evaluate(() => window.readVisibleCell(1, 3));
    assert(
      savedAgain?.chars === 'T'
        && savedAgain.fg === 1
        && savedAgain.bold
        && !savedAgain.italic,
      'saved cursor was not retained as xterm.js single-slot state',
      savedAgain,
    );

    await writeTerminal(page, `${esc}[?1049l`);
    const normal = await page.evaluate(() => window.readTerminalState());
    assert(normal.type === 'normal', 'leave-alternate delta did not restore the normal buffer', normal);
    assert(
      JSON.stringify(normal.cursor) === JSON.stringify(fixture.normal_cursor),
      'normal cursor was not restored after leaving alternate',
      { actual: normal.cursor, expected: fixture.normal_cursor },
    );
    assert(
      JSON.stringify(normal.visible) === JSON.stringify(fixture.normal_display),
      'normal visible content changed during alternate snapshot replay',
      { actual: normal.visible, expected: fixture.normal_display },
    );
    assert(
      JSON.stringify(normal.history) === JSON.stringify(fixture.normal_history),
      'normal scrollback was cleared or changed by alternate snapshot replay',
      { actual: normal.history, expected: fixture.normal_history, baseY: normal.baseY },
    );

    await writeTerminal(page, 'N');
    const normalCell = await page.evaluate(() => window.readVisibleCell(9, 5));
    assert(
      normalCell?.chars === 'N' && normalCell.fg === 4 && !normalCell.bold && !normalCell.italic,
      'normal cursor/SGR could not continue with a raw delta after leaving alternate',
      normalCell,
    );

    await page.evaluate(() => window.resetTerminal({ cols: 5, rows: 3 }));
    await writeTerminal(page, fixture.pending_wrap_snapshot);
    await writeTerminal(page, 'Z');
    const pendingWrap = await page.evaluate(() => window.readTerminalState());
    assert(
      pendingWrap.visible[0] === 'abcde'
        && pendingWrap.visible[1] === 'Z'
        && JSON.stringify(pendingWrap.cursor) === JSON.stringify([1, 1]),
      'pending autowrap was lost across the authoritative snapshot',
      pendingWrap,
    );

    await page.evaluate(() => window.resetTerminal({ cols: 20, rows: 3 }));
    await writeTerminal(page, fixture.charset_snapshot);
    await writeTerminal(page, 'k');
    const charset = await page.evaluate(() => window.readTerminalState());
    assert(
      charset.visible[0] === '┌─┐',
      'DEC special-graphics charset did not continue after the snapshot',
      charset,
    );

    await page.evaluate(() => window.resetTerminal({ cols: 120, rows: 3 }));
    await writeTerminal(page, fixture.expanded_tabs_snapshot);
    await writeTerminal(page, '\tX');
    const expandedTabCell = await page.evaluate(() => window.readVisibleCell(80, 0));
    assert(
      expandedTabCell?.chars === 'X',
      'expanded terminal snapshot did not restore new default tab stops',
      expandedTabCell,
    );

    await page.evaluate(() => window.resetTerminal({ cols: 20, rows: 3 }));
    await writeTerminal(page, fixture.extended_sgr_snapshot);
    await writeTerminal(page, 'X');
    const extendedSgrCell = await page.evaluate(() => window.readVisibleCell(1, 0));
    assert(
      extendedSgrCell?.chars === 'X'
        && extendedSgrCell.dim === true
        && extendedSgrCell.invisible === true,
      'dim/conceal SGR did not continue after the snapshot',
      extendedSgrCell,
    );

    await page.evaluate(() => window.resetTerminal({ cols: 20, rows: 3 }));
    await writeTerminal(page, fixture.rich_sgr_snapshot);
    await writeTerminal(page, 'X');
    const richSgrCell = await page.evaluate(() => window.readVisibleCell(1, 0));
    assert(
      richSgrCell?.chars === 'X'
        && richSgrCell.underline === true
        && richSgrCell.underlineStyle === 3
        && richSgrCell.underlineColor === 0x010203
        && richSgrCell.overline === true
        && richSgrCell.bold === false
        && richSgrCell.italic === false
        && richSgrCell.dim === false,
      'extended underline/overline SGR did not continue after the snapshot',
      richSgrCell,
    );

    await page.evaluate(() => window.resetTerminal({ cols: 20, rows: 6 }));
    await writeTerminal(page, fixture.isolated_alternate_snapshot);
    const isolatedAlternate = await page.evaluate(() => window.readTerminalState());
    assert(
      isolatedAlternate.type === 'alternate' && isolatedAlternate.visible[0] === 'A       B',
      'alternate buffer did not start with independent default tab stops and margins',
      isolatedAlternate,
    );
    await writeTerminal(page, `${esc}[6G${esc}H${esc}[?1049l${esc}[HA\tB${esc}[?6h${esc}[HZ`);
    const isolatedNormal = await page.evaluate(() => ({
      tabbed: window.readVisibleCell(10, 0),
      origin: window.readVisibleCell(0, 1),
      state: window.readTerminalState(),
    }));
    assert(
      isolatedNormal.state.type === 'normal'
        && isolatedNormal.tabbed?.chars === 'B'
        && isolatedNormal.origin?.chars === 'Z',
      'alternate HTS/margins polluted the saved normal buffer state',
      isolatedNormal,
    );
    assert(pageErrors.length === 0, 'Chromium reported errors', pageErrors);
    console.log('terminal snapshot xterm regression passed');
  } finally {
    await browser?.close();
  }
}

run().catch(error => {
  console.error(error?.stack || String(error));
  process.exitCode = 1;
});
