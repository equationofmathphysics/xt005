import copy
import unicodedata
from collections import defaultdict, deque
from typing import NamedTuple

import pyte
import pyte.charsets as terminal_charsets
import pyte.control as terminal_control
import pyte.graphics as terminal_graphics
import pyte.modes as terminal_modes
from pyte.screens import Margins
import regex
from wcwidth import wcswidth


_BASIC_FG = {
    "black": 30,
    "red": 31,
    "green": 32,
    "brown": 33,
    "blue": 34,
    "magenta": 35,
    "cyan": 36,
    "white": 37,
    "brightblack": 90,
    "brightred": 91,
    "brightgreen": 92,
    "brightbrown": 93,
    "brightblue": 94,
    "brightmagenta": 95,
    "brightcyan": 96,
    "brightwhite": 97,
}
_BASIC_BG = {
    name: code + 10 if code < 90 else code + 10
    for name, code in _BASIC_FG.items()
}


def _color_sgr(value, foreground):
    if not value or value == "default":
        return 39 if foreground else 49
    basic = (_BASIC_FG if foreground else _BASIC_BG).get(value)
    if basic is not None:
        return basic
    if len(value) == 6:
        try:
            red = int(value[0:2], 16)
            green = int(value[2:4], 16)
            blue = int(value[4:6], 16)
        except ValueError:
            return 39 if foreground else 49
        prefix = 38 if foreground else 48
        return f"{prefix};2;{red};{green};{blue}"
    return 39 if foreground else 49


def _underline_color_sgr(value):
    if not value or value == "default" or len(value) != 6:
        return None
    try:
        red = int(value[0:2], 16)
        green = int(value[2:4], 16)
        blue = int(value[4:6], 16)
    except ValueError:
        return None
    return f"58;2;{red};{green};{blue}"


def _style_sgr(char):
    values = [0]
    if char.bold:
        values.append(1)
    if getattr(char, "dim", False):
        values.append(2)
    if char.italics:
        values.append(3)
    underline_style = int(getattr(char, "underline_style", 0) or 0)
    if underline_style:
        values.append(4 if underline_style == 1 else f"4:{underline_style}")
    elif char.underscore:
        values.append(4)
    if char.blink:
        values.append(5)
    if char.reverse:
        values.append(7)
    if char.strikethrough:
        values.append(9)
    if getattr(char, "conceal", False):
        values.append(8)
    if getattr(char, "overline", False):
        values.append(53)
    values.append(_color_sgr(char.fg, True))
    values.append(_color_sgr(char.bg, False))
    underline_color = _underline_color_sgr(getattr(char, "underline_color", "default"))
    if underline_color:
        values.append(underline_color)
    return "\x1b[" + ";".join(str(value) for value in values) + "m"


_GRAPHEME_RE = regex.compile(r"\X")
_PRIVATE_MODES = (
    1,      # Application cursor keys.
    7,      # Auto wrap.
    9,      # X10 mouse tracking.
    12,     # Cursor blinking.
    25,     # Cursor visibility.
    45,     # Reverse wraparound.
    66,     # Application keypad.
    1000,
    1001,
    1002,
    1003,
    1004,
    1005,
    1006,
    1015,
    1016,
    2004,
    2026,
)
_NON_PRIVATE_MODES = (4, 20)


class TerminalChar(NamedTuple):
    data: str
    fg: str = "default"
    bg: str = "default"
    bold: bool = False
    italics: bool = False
    underscore: bool = False
    strikethrough: bool = False
    reverse: bool = False
    blink: bool = False
    dim: bool = False
    conceal: bool = False
    overline: bool = False
    underline_style: int = 0
    underline_color: str = "default"


def _parse_csi_parameter(value):
    if ":" not in value:
        return min(int(value or 0), 9999)
    return tuple(
        None if part == "" else min(int(part), 9999)
        for part in value.split(":")
    )


class ExtendedSgrStream(pyte.Stream):
    """pyte stream with ECMA-48 SGR subparameter support."""

    csi = {
        **pyte.Stream.csi,
        "S": "scroll_up",
        "T": "scroll_down",
    }

    def _parser_fsm(self):
        basic = self.basic
        listener = self.listener
        if listener is None:
            raise RuntimeError("Listener is not set")
        draw = listener.draw
        debug = listener.debug

        esc = terminal_control.ESC
        csi_c1 = terminal_control.CSI_C1
        osc_c1 = terminal_control.OSC_C1
        sp_or_gt = terminal_control.SP + ">"
        nul_or_del = terminal_control.NUL + terminal_control.DEL
        can_or_sub = terminal_control.CAN + terminal_control.SUB
        allowed_in_csi = "".join((
            terminal_control.BEL,
            terminal_control.BS,
            terminal_control.HT,
            terminal_control.LF,
            terminal_control.VT,
            terminal_control.FF,
            terminal_control.CR,
        ))
        osc_terminators = {
            terminal_control.ST_C0,
            terminal_control.ST_C1,
            terminal_control.BEL,
        }

        def create_dispatcher(mapping):
            return defaultdict(
                lambda: debug,
                (
                    (event, getattr(listener, event_name))
                    for event, event_name in mapping.items()
                ),
            )

        basic_dispatch = create_dispatcher(basic)
        sharp_dispatch = create_dispatcher(self.sharp)
        escape_dispatch = create_dispatcher(self.escape)
        csi_dispatch = create_dispatcher(self.csi)

        while True:
            char = yield True

            if char == esc:
                char = yield None
                if char == "[":
                    char = csi_c1
                elif char == "]":
                    char = osc_c1
                else:
                    if char == "#":
                        sharp_dispatch[(yield None)]()
                    elif char == "%":
                        self.select_other_charset((yield None))
                    elif char in "()":
                        code = yield None
                        if self.use_utf8:
                            continue
                        listener.define_charset(code, mode=char)
                    else:
                        escape_dispatch[char]()
                    continue

            if char in basic:
                if char in (terminal_control.SI, terminal_control.SO) and self.use_utf8:
                    continue
                basic_dispatch[char]()
            elif char == csi_c1:
                raw_params = []
                current = ""
                private = False
                while True:
                    char = yield None
                    if char == "?":
                        private = True
                    elif char in allowed_in_csi:
                        basic_dispatch[char]()
                    elif char in sp_or_gt:
                        pass
                    elif char in can_or_sub:
                        draw(char)
                        break
                    elif char.isdigit() or char == ":":
                        current += char
                    elif char == "$":
                        yield None
                        break
                    else:
                        if char == ";":
                            raw_params.append(current)
                            current = ""
                        else:
                            raw_params.append(current)
                            params = [_parse_csi_parameter(value) for value in raw_params]
                            if char != "m":
                                params = [
                                    value if isinstance(value, int) else (value[0] or 0)
                                    for value in params
                                ]
                            if private:
                                csi_dispatch[char](*params, private=True)
                            else:
                                csi_dispatch[char](*params)
                            break
            elif char == osc_c1:
                code = yield None
                if code == "R" or code == "P":
                    continue

                param = ""
                while True:
                    char = yield None
                    if char == esc:
                        char += yield None
                    if char in osc_terminators:
                        break
                    param += char

                param = param[1:]
                if code in "01":
                    listener.set_icon_name(param)
                if code in "02":
                    listener.set_title(param)
            elif char not in nul_or_del:
                draw(char)


class UnicodeHistoryScreen(pyte.HistoryScreen):
    """HistoryScreen variant which keeps whole Unicode grapheme clusters."""

    _ALT_BUFFER_MODES = {47, 1047, 1049}

    def __init__(self, *args, **kwargs):
        self._normal_buffer_state = None
        super().__init__(*args, **kwargs)

    @property
    def default_char(self):
        return TerminalChar(
            data=" ",
            reverse=terminal_modes.DECSCNM in self.mode,
        )

    def reset(self):
        super().reset()
        if not isinstance(self.cursor.attrs, TerminalChar):
            self.cursor.attrs = TerminalChar(**self.cursor.attrs._asdict())

    def _capture_buffer_state(self):
        return {
            "buffer": self.buffer,
            "cursor": self.cursor,
            "savepoints": self.savepoints,
            "history": self.history,
            "margins": self.margins,
            "mode": self.mode,
            "tabstops": self.tabstops,
            "title": self.title,
            "icon_name": self.icon_name,
            "charset": self.charset,
            "g0_charset": self.g0_charset,
            "g1_charset": self.g1_charset,
            "saved_columns": self.saved_columns,
        }

    def _restore_buffer_state(self, state):
        for name, value in state.items():
            setattr(self, name, value)

    def _enter_alternate_buffer(self, modes):
        if self._normal_buffer_state is not None:
            return
        self._normal_buffer_state = self._capture_buffer_state()
        history = self.history
        self.buffer = defaultdict(self.buffer.default_factory)
        self.history = type(history)(
            deque(maxlen=history.size),
            deque(maxlen=history.size),
            history.ratio,
            history.size,
            history.size,
        )
        self.savepoints = []
        self.cursor = copy.copy(self.cursor)
        self.margins = None
        self.tabstops = set(range(8, self.columns, 8))
        self.mode.update(mode << 5 for mode in modes)
        self.dirty.update(range(self.lines))

    def _leave_alternate_buffer(self):
        state = self._normal_buffer_state
        if state is None:
            return
        self._normal_buffer_state = None
        self._restore_buffer_state(state)
        self.mode.difference_update(mode << 5 for mode in self._ALT_BUFFER_MODES)
        self.dirty.update(range(self.lines))

    def set_mode(self, *modes, **kwargs):
        mode_list = list(modes)
        if kwargs.get("private") and self._ALT_BUFFER_MODES.intersection(mode_list):
            self._enter_alternate_buffer(self._ALT_BUFFER_MODES.intersection(mode_list))
            mode_list = [mode for mode in mode_list if mode not in self._ALT_BUFFER_MODES]
        if mode_list:
            super().set_mode(*mode_list, **kwargs)

    def reset_mode(self, *modes, **kwargs):
        mode_list = list(modes)
        if kwargs.get("private") and self._ALT_BUFFER_MODES.intersection(mode_list):
            self._leave_alternate_buffer()
            mode_list = [mode for mode in mode_list if mode not in self._ALT_BUFFER_MODES]
        if mode_list:
            super().reset_mode(*mode_list, **kwargs)

    def save_cursor(self):
        super().save_cursor()
        if len(self.savepoints) > 1:
            self.savepoints[:] = self.savepoints[-1:]

    def restore_cursor(self):
        if not self.savepoints:
            self.cursor_position()
            return
        savepoint = self.savepoints[-1]
        self.g0_charset = savepoint.g0_charset
        self.g1_charset = savepoint.g1_charset
        self.charset = savepoint.charset
        cursor_hidden = self.cursor.hidden
        self.cursor = copy.copy(savepoint.cursor)
        self.cursor.hidden = cursor_hidden
        self.ensure_hbounds()
        self.ensure_vbounds(use_margins=True)
        self.savepoints[:] = [savepoint._replace(cursor=copy.copy(savepoint.cursor))]

    @staticmethod
    def _sgr_color(parts, *, colon=False):
        if not parts:
            return None
        mode = int(parts[0] or 0)
        if mode == 5 and len(parts) >= 2 and parts[1] is not None:
            return terminal_graphics.FG_BG_256[int(parts[1]) & 0xff]
        if mode != 2:
            return None

        components = list(parts[1:])
        if colon and len(components) >= 4:
            components = components[-3:]
        else:
            components = components[:3]
        if len(components) != 3:
            return None
        components = [max(0, min(255, int(value or 0))) for value in components]
        return "{:02x}{:02x}{:02x}".format(*components)

    def select_graphic_rendition(self, *attrs):
        values = list(attrs or (0,))
        index = 0
        while index < len(values):
            value = values[index]
            if isinstance(value, tuple):
                attr = int(value[0] or 0)
                if attr == 4:
                    style = int(value[1] or 0) if len(value) > 1 else 1
                    style = style if 0 <= style <= 5 else 1
                    self.cursor.attrs = self.cursor.attrs._replace(
                        underscore=bool(style),
                        underline_style=style,
                    )
                    index += 1
                    continue
                if attr in (38, 48, 58):
                    color = self._sgr_color(value[1:], colon=True)
                    if color is not None:
                        field = {38: "fg", 48: "bg", 58: "underline_color"}[attr]
                        self.cursor.attrs = self.cursor.attrs._replace(**{field: color})
                    index += 1
                    continue
            else:
                attr = int(value or 0)

            if attr in (38, 48, 58):
                remaining = values[index + 1:]
                mode = remaining[0] if remaining and isinstance(remaining[0], int) else None
                consumed = min(len(remaining), 4 if mode == 2 else 2 if mode == 5 else 1)
                color_parts = remaining[:consumed]
                color = self._sgr_color(color_parts)
                if color is not None:
                    field = {38: "fg", 48: "bg", 58: "underline_color"}[attr]
                    self.cursor.attrs = self.cursor.attrs._replace(**{field: color})
                index += consumed + 1
                continue

            if attr == 0:
                self.cursor.attrs = self.default_char
            elif attr == 2:
                self.cursor.attrs = self.cursor.attrs._replace(dim=True)
            elif attr == 4:
                self.cursor.attrs = self.cursor.attrs._replace(underscore=True, underline_style=1)
            elif attr == 8:
                self.cursor.attrs = self.cursor.attrs._replace(conceal=True)
            elif attr == 21:
                self.cursor.attrs = self.cursor.attrs._replace(underscore=True, underline_style=2)
            elif attr == 22:
                self.cursor.attrs = self.cursor.attrs._replace(bold=False, dim=False)
            elif attr == 24:
                self.cursor.attrs = self.cursor.attrs._replace(underscore=False, underline_style=0)
            elif attr == 28:
                self.cursor.attrs = self.cursor.attrs._replace(conceal=False)
            elif attr == 53:
                self.cursor.attrs = self.cursor.attrs._replace(overline=True)
            elif attr == 55:
                self.cursor.attrs = self.cursor.attrs._replace(overline=False)
            elif attr == 59:
                self.cursor.attrs = self.cursor.attrs._replace(underline_color="default")
            elif attr in terminal_graphics.FG_ANSI:
                self.cursor.attrs = self.cursor.attrs._replace(fg=terminal_graphics.FG_ANSI[attr])
            elif attr in terminal_graphics.BG_ANSI:
                self.cursor.attrs = self.cursor.attrs._replace(bg=terminal_graphics.BG_ANSI[attr])
            elif attr in terminal_graphics.TEXT:
                attr_name = terminal_graphics.TEXT[attr]
                self.cursor.attrs = self.cursor.attrs._replace(
                    **{attr_name[1:]: attr_name.startswith("+")}
                )
            elif attr in terminal_graphics.FG_AIXTERM:
                self.cursor.attrs = self.cursor.attrs._replace(fg=terminal_graphics.FG_AIXTERM[attr])
            elif attr in terminal_graphics.BG_AIXTERM:
                self.cursor.attrs = self.cursor.attrs._replace(bg=terminal_graphics.BG_AIXTERM[attr])
            index += 1

    def _resize_current_buffer(self, lines=None, columns=None):
        old_columns = self.columns
        saved_cursor = self.savepoints[-1] if self.savepoints else None
        super().resize(lines=lines, columns=columns)
        self.ensure_hbounds()
        self.ensure_vbounds()
        self.tabstops = {
            column for column in self.tabstops
            if 0 <= column < self.columns
        }
        if self.columns > old_columns:
            first_new_stop = ((old_columns + 7) // 8) * 8
            self.tabstops.update(range(first_new_stop, self.columns, 8))
        if saved_cursor is not None:
            cursor = copy.copy(saved_cursor.cursor)
            cursor.x = min(max(0, cursor.x), self.columns - 1)
            cursor.y = min(max(0, cursor.y), self.lines - 1)
            self.savepoints[:] = [saved_cursor._replace(cursor=cursor)]
        else:
            self.savepoints[:] = []

    def resize(self, lines=None, columns=None):
        if self._normal_buffer_state is None:
            self._resize_current_buffer(lines=lines, columns=columns)
            return

        old_lines, old_columns = self.lines, self.columns
        alternate_state = self._capture_buffer_state()
        normal_state = self._normal_buffer_state

        self._normal_buffer_state = None
        self._restore_buffer_state(normal_state)
        self._resize_current_buffer(lines=lines, columns=columns)
        resized_normal_state = self._capture_buffer_state()

        self._restore_buffer_state(alternate_state)
        self.lines, self.columns = old_lines, old_columns
        self._normal_buffer_state = resized_normal_state
        self._resize_current_buffer(lines=lines, columns=columns)

    def scroll_up(self, count=None):
        count = max(1, int(count or 1))
        top, bottom = self.margins or Margins(0, self.lines - 1)
        count = min(count, bottom - top + 1)
        self.before_event("scroll_up")
        try:
            self.dirty.update(range(top, bottom + 1))
            for _ in range(count):
                self.history.top.append(self.buffer[top])
                for row in range(top, bottom):
                    self.buffer[row] = self.buffer[row + 1]
                self.buffer.pop(bottom, None)
        finally:
            self.after_event("scroll_up")

    def scroll_down(self, count=None):
        count = max(1, int(count or 1))
        top, bottom = self.margins or Margins(0, self.lines - 1)
        count = min(count, bottom - top + 1)
        self.before_event("scroll_down")
        try:
            self.dirty.update(range(top, bottom + 1))
            for _ in range(count):
                self.history.bottom.append(self.buffer[bottom])
                for row in range(bottom, top, -1):
                    self.buffer[row] = self.buffer[row - 1]
                self.buffer.pop(top, None)
        finally:
            self.after_event("scroll_down")

    def _previous_cell(self):
        row = self.cursor.y
        column = min(self.cursor.x, self.columns) - 1
        while row >= 0:
            line = self.buffer[row]
            while column >= 0:
                char = line.get(column, self.default_char)
                if char.data:
                    return line, column, char
                column -= 1
            row -= 1
            column = self.columns - 1
        return None

    @property
    def display(self):
        lines = []
        for row in range(self.lines):
            parts = []
            for column in range(self.columns):
                data = self.buffer[row][column].data
                if data:
                    parts.append(data)
            lines.append("".join(parts))
        return lines

    def _append_to_previous_cell(self, text):
        previous = self._previous_cell()
        if previous is None:
            return False
        line, column, char = previous
        old_width = max(1, wcswidth(char.data))
        combined = unicodedata.normalize("NFC", char.data + text)
        new_width = max(1, min(2, wcswidth(combined)))
        line[column] = char._replace(data=combined)
        if new_width == 2 and column + 1 < self.columns:
            line[column + 1] = char._replace(data="")
        if self.cursor.y >= 0 and self.cursor.x == column + old_width:
            self.cursor.x = min(self.columns, self.cursor.x + new_width - old_width)
        self.dirty.add(self.cursor.y)
        return True

    def draw(self, data):
        data = data.translate(self.g1_charset if self.charset else self.g0_charset)
        for grapheme in _GRAPHEME_RE.findall(data):
            grapheme = unicodedata.normalize("NFC", grapheme)
            char_width = wcswidth(grapheme)
            previous = self._previous_cell()
            if char_width == 0:
                self._append_to_previous_cell(grapheme)
                continue
            if previous is not None and previous[2].data.endswith("\u200d"):
                self._append_to_previous_cell(grapheme)
                continue
            if char_width < 0:
                continue
            char_width = min(char_width, 2)

            if self.cursor.x == self.columns:
                if terminal_modes.DECAWM in self.mode:
                    self.dirty.add(self.cursor.y)
                    self.carriage_return()
                    self.linefeed()
                elif char_width > 0:
                    self.cursor.x -= char_width

            if terminal_modes.IRM in self.mode and char_width > 0:
                self.insert_characters(char_width)

            line = self.buffer[self.cursor.y]
            line[self.cursor.x] = self.cursor.attrs._replace(data=grapheme)
            if char_width == 2 and self.cursor.x + 1 < self.columns:
                line[self.cursor.x + 1] = self.cursor.attrs._replace(data="")
            self.cursor.x = min(self.cursor.x + char_width, self.columns)

        self.dirty.add(self.cursor.y)


class TerminalScreenModel:
    def __init__(self, cols=80, rows=24, history_lines=10000):
        self.history_lines = max(0, int(history_lines))
        self.screen = UnicodeHistoryScreen(
            max(2, int(cols)),
            max(1, int(rows)),
            history=self.history_lines,
        )
        self.stream = ExtendedSgrStream(self.screen)
        self.stream.use_utf8 = False

    @property
    def cols(self):
        return self.screen.columns

    @property
    def rows(self):
        return self.screen.lines

    @property
    def active_buffer(self):
        return "alternate" if self.screen._normal_buffer_state is not None else "normal"

    @property
    def delta_safe(self):
        # pyte keeps a generator suspended while parsing an incomplete CSI/OSC.
        # A screen snapshot cannot serialize that parser continuation.
        return self.stream._taking_plain_text is True

    def feed(self, text):
        if text:
            self.stream.feed(str(text))

    def resize(self, cols, rows):
        cols = max(2, int(cols))
        rows = max(1, int(rows))
        if cols == self.cols and rows == self.rows:
            return
        self.screen.resize(lines=rows, columns=cols)

    def _serialize_line(self, line):
        if line is None:
            return ""
        last_column = -1
        for column in range(self.cols):
            char = line.get(column, self.screen.default_char)
            if (
                char.data.strip()
                or char.bg != "default"
                or char.reverse
                or char.strikethrough
                or char.underscore
                or getattr(char, "overline", False)
            ):
                last_column = column
        if last_column < 0:
            return ""

        parts = []
        previous_style = None
        for column in range(last_column + 1):
            char = line.get(column, self.screen.default_char)
            style = (
                char.fg,
                char.bg,
                char.bold,
                char.italics,
                char.underscore,
                char.strikethrough,
                char.reverse,
                char.blink,
                getattr(char, "dim", False),
                getattr(char, "conceal", False),
                getattr(char, "overline", False),
                getattr(char, "underline_style", 0),
                getattr(char, "underline_color", "default"),
            )
            if style != previous_style:
                parts.append(_style_sgr(char))
                previous_style = style
            if char.data == "":
                previous = line.get(column - 1, self.screen.default_char) if column else None
                if previous is not None and wcswidth(previous.data) == 2:
                    continue
                parts.append(" ")
            else:
                parts.append(char.data)
        parts.append("\x1b[0m")
        return "".join(parts)

    @staticmethod
    def _state_value(state, name, default=None):
        if isinstance(state, dict):
            return state.get(name, default)
        return getattr(state, name, default)

    @staticmethod
    def _charset_code(charset):
        for code, mapping in terminal_charsets.MAPS.items():
            if charset == mapping:
                return code
        return "B"

    def _serialize_charset_state(self, state):
        g0 = self._charset_code(self._state_value(state, "g0_charset", terminal_charsets.LAT1_MAP))
        g1 = self._charset_code(self._state_value(state, "g1_charset", terminal_charsets.VT100_MAP))
        active = int(self._state_value(state, "charset", 0) or 0)
        return f"\x1b({g0}\x1b){g1}" + ("\x0e" if active else "\x0f")

    def _serialize_modes(self, mode, *, include_origin=True):
        active = set(mode or ())
        parts = []
        enabled = [value for value in _NON_PRIVATE_MODES if value in active]
        disabled = [value for value in _NON_PRIVATE_MODES if value not in active]
        if enabled:
            parts.append("\x1b[" + ";".join(map(str, enabled)) + "h")
        if disabled:
            parts.append("\x1b[" + ";".join(map(str, disabled)) + "l")

        private_modes = [value for value in _PRIVATE_MODES if value != 25]
        if not include_origin:
            private_modes = [value for value in private_modes if value != 6]
        enabled = [value for value in private_modes if value << 5 in active]
        disabled = [value for value in private_modes if value << 5 not in active]
        if enabled:
            parts.append("\x1b[?" + ";".join(map(str, enabled)) + "h")
        if disabled:
            parts.append("\x1b[?" + ";".join(map(str, disabled)) + "l")
        return "".join(parts)

    def _serialize_margins(self, margins):
        if margins is None:
            return "\x1b[r"
        top = max(0, min(self.rows - 1, int(margins.top)))
        bottom = max(top, min(self.rows - 1, int(margins.bottom)))
        return f"\x1b[{top + 1};{bottom + 1}r"

    def _serialize_tabstops(self, tabstops):
        parts = ["\x1b[3g"]
        for column in sorted(set(tabstops or ())):
            column = int(column)
            if 0 <= column < self.cols:
                parts.append(f"\x1b[{column + 1}G\x1bH")
        return "".join(parts)

    def _serialize_cursor_position(self, cursor, margins, origin):
        row = max(0, min(self.rows - 1, int(cursor.y)))
        column = max(0, min(self.cols - 1, int(cursor.x)))
        if origin and margins is not None:
            row = max(0, min(margins.bottom - margins.top, row - margins.top))
        return f"\x1b[{row + 1};{column + 1}H"

    def _serialize_cursor_state(self, cursor, margins, origin):
        return "".join((
            self._serialize_cursor_position(cursor, margins, origin),
            _style_sgr(cursor.attrs),
            "\x1b[?25l" if cursor.hidden else "\x1b[?25h",
        ))

    def _serialize_cursor_state_with_wrap(self, state, cursor, margins, origin):
        if int(cursor.x) < self.cols:
            return self._serialize_cursor_state(cursor, margins, origin)

        buffer = self._state_value(state, "buffer", {})
        row = max(0, min(self.rows - 1, int(cursor.y)))
        line = buffer[row]
        column = self.cols - 1
        char = line.get(column, self.screen.default_char)
        while column > 0 and not char.data:
            column -= 1
            char = line.get(column, self.screen.default_char)

        position = copy.copy(cursor)
        position.x = column
        parts = [
            "\x1b[4l",
            self._serialize_cursor_position(position, margins, origin),
            _style_sgr(char),
            char.data or " ",
        ]
        mode = set(self._state_value(state, "mode", ()))
        if terminal_modes.IRM in mode:
            parts.append("\x1b[4h")
        parts.extend((
            _style_sgr(cursor.attrs),
            "\x1b[?25l" if cursor.hidden else "\x1b[?25h",
        ))
        return "".join(parts)

    def _serialize_runtime_state(self, state):
        mode = set(self._state_value(state, "mode", ()))
        margins = self._state_value(state, "margins")
        cursor = self._state_value(state, "cursor")
        savepoints = list(self._state_value(state, "savepoints", ()) or ())
        if len(savepoints) > 1:
            savepoints = savepoints[-1:]
        parts = [
            self._serialize_modes(mode, include_origin=False),
            self._serialize_margins(margins),
            self._serialize_tabstops(self._state_value(state, "tabstops", ())),
        ]

        for savepoint in savepoints:
            parts.append("\x1b[?7h" if savepoint.wrap else "\x1b[?7l")
            parts.append("\x1b[?6h" if savepoint.origin else "\x1b[?6l")
            parts.append(self._serialize_charset_state(savepoint))
            parts.append(self._serialize_cursor_state_with_wrap(
                state,
                savepoint.cursor,
                margins,
                savepoint.origin,
            ))
            parts.append("\x1b7")

        origin = terminal_modes.DECOM in mode
        parts.append("\x1b[?7h" if terminal_modes.DECAWM in mode else "\x1b[?7l")
        parts.append("\x1b[?6h" if origin else "\x1b[?6l")
        parts.append(self._serialize_charset_state(state))
        parts.append(self._serialize_cursor_state_with_wrap(state, cursor, margins, origin))
        return "".join(parts)

    def _serialize_buffer(self, state, history_lines=None, *, clear_scrollback=True):
        parts = [
            "\x1b[?25l\x1b[?6l\x1b[4l\x1b[r\x1b[0m",
            "\x1b(B\x1b)0\x0f\x1b[2J",
        ]
        if clear_scrollback:
            parts.append("\x1b[3J")
        parts.append("\x1b[H")
        history_state = self._state_value(state, "history")
        history = list(history_state.top) if history_state is not None else []
        if history_lines is not None:
            history = history[-max(0, int(history_lines)):]
        buffer = self._state_value(state, "buffer", {})
        lines = history + [buffer[row] for row in range(self.rows)]
        for index, line in enumerate(lines):
            parts.append(self._serialize_line(line))
            if index + 1 < len(lines):
                parts.append("\r\n")
        parts.append(self._serialize_runtime_state(state))
        return "".join(parts)

    def serialize(self, history_lines=None):
        current = self.screen._capture_buffer_state()
        normal = self.screen._normal_buffer_state
        parts = ["\x1b[?25l\x1b[?1049l"]
        if normal is None:
            parts.append(self._serialize_buffer(current, history_lines))
        else:
            parts.append(self._serialize_buffer(normal, history_lines))
            parts.append("\x1b[?1049h")
            parts.append(self._serialize_buffer(current, history_lines, clear_scrollback=False))
        return "".join(parts)

    def serialize_bounded(self, max_chars):
        max_chars = max(1, int(max_chars))
        history_lines = len(self.screen.history.top)
        normal = self.screen._normal_buffer_state
        if normal is not None:
            history_lines = max(history_lines, len(normal["history"].top))
        while True:
            snapshot = self.serialize(history_lines)
            if len(snapshot) <= max_chars:
                return snapshot
            if not history_lines:
                return ""
            history_lines //= 2
