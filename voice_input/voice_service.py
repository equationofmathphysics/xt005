from __future__ import annotations

import argparse
import logging
import os
import platform
from queue import Queue
import re
import shutil
import signal
import struct
import subprocess
import threading
import time
import wave
from dataclasses import dataclass
from typing import Callable

from .doubao_asr import DoubaoASRClient, DoubaoASRConfig

LOGGER = logging.getLogger(__name__)
_CLIPBOARD_ROOT = None


@dataclass
class ServiceConfig:
    hotkey: str
    sample_rate: int
    max_seconds: float
    min_seconds: float
    inject: str
    live: bool
    save_recordings: bool
    recordings_dir: str
    recordings_keep: int


class AudioRecorder:
    def __init__(
        self, sample_rate: int, on_chunk: Callable[[bytes], None] | None = None
    ) -> None:
        self.sample_rate = sample_rate
        self.on_chunk = on_chunk
        self._frames: list[bytes] = []
        self._stream = None
        self._started_at = 0.0
        self._lock = threading.Lock()

    @property
    def duration(self) -> float:
        if not self._started_at:
            return 0.0
        return time.monotonic() - self._started_at

    def start(self) -> None:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("missing dependency: pip install sounddevice") from exc

        with self._lock:
            self._frames.clear()
            self._started_at = time.monotonic()

        def callback(indata, frames, callback_time, status) -> None:  # noqa: ANN001
            if status:
                LOGGER.warning("audio callback status: %s", status)
            chunk = indata.copy().tobytes()
            with self._lock:
                self._frames.append(chunk)
            if self.on_chunk is not None:
                self.on_chunk(chunk)

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            blocksize=max(1, self.sample_rate // 10),
            channels=1,
            dtype="int16",
            callback=callback,
        )
        self._stream.start()

    def stop(self) -> bytes:
        stream = self._stream
        self._stream = None
        if stream is not None:
            stream.stop()
            stream.close()
        with self._lock:
            data = b"".join(self._frames)
            self._frames.clear()
            self._started_at = 0.0
        return data


class VoiceTypingService:
    def __init__(self, config: ServiceConfig, asr_client: DoubaoASRClient) -> None:
        self.config = config
        self.asr_client = asr_client
        self.recorder = AudioRecorder(config.sample_rate)
        self._state = "idle"
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._live_queue: Queue[bytes | None] | None = None
        self._live_thread: threading.Thread | None = None
        self._live_cancelled = False
        self._live_last_text = ""
        self._stop_requested = False

    def toggle(self) -> None:
        with self._lock:
            state = self._state
        if state == "idle":
            self.start_recording()
        elif state == "recording":
            self.stop_recording()
        else:
            LOGGER.info("busy: currently %s", state)

    def start_recording(self) -> None:
        with self._lock:
            if self._state != "idle":
                return
            self._state = "recording"
            self._live_cancelled = False
            self._live_last_text = ""
            self._stop_requested = False

        if self.config.live:
            self._live_queue = Queue()
            self.recorder.on_chunk = self._enqueue_live_audio
            self._live_thread = threading.Thread(
                target=self._run_live_transcription,
                name="voice-input-live-asr",
                daemon=True,
            )
            self._live_thread.start()
        else:
            self.recorder.on_chunk = None

        try:
            self.recorder.start()
        except Exception:
            with self._lock:
                self._state = "idle"
            self._finish_live_queue(cancelled=True)
            raise

        notify("Voice input", "Recording started")
        LOGGER.info("recording started live=%s", self.config.live)
        if self.config.max_seconds > 0:
            self._timer = threading.Timer(self.config.max_seconds, self.stop_recording)
            self._timer.daemon = True
            self._timer.start()

    def stop_recording(self) -> None:
        with self._lock:
            if self._state != "recording":
                return
            self._state = "transcribing"

        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

        duration = self.recorder.duration
        audio = self.recorder.stop()
        self.recorder.on_chunk = None
        LOGGER.info("recording stopped: %.2fs, %d bytes", duration, len(audio))

        if duration < self.config.min_seconds:
            notify("Voice input", "Recording ignored: too short")
            self._finish_live_queue(cancelled=True)
            return

        self._save_recording(audio, duration)

        if self.config.live:
            notify("Voice input", "Finishing transcription")
            self._finish_live_queue(cancelled=False)
            return

        thread = threading.Thread(
            target=self._transcribe_and_inject,
            args=(audio,),
            name="voice-input-transcribe",
            daemon=True,
        )
        thread.start()

    def _save_recording(self, audio: bytes, duration: float) -> None:
        if not self.config.save_recordings or not audio:
            return
        try:
            path = save_wav_recording(
                audio,
                sample_rate=self.config.sample_rate,
                recordings_dir=self.config.recordings_dir,
                keep=self.config.recordings_keep,
            )
            LOGGER.info("saved recording: %s duration=%.2fs", path, duration)
        except Exception:
            LOGGER.exception("failed to save recording")

    def _enqueue_live_audio(self, chunk: bytes) -> None:
        queue = self._live_queue
        if queue is not None:
            queue.put(chunk)

    def _finish_live_queue(self, cancelled: bool) -> None:
        with self._lock:
            self._live_cancelled = cancelled
            self._stop_requested = True
        queue = self._live_queue
        if queue is not None:
            queue.put(None)
        if queue is None:
            with self._lock:
                self._state = "idle"

    def _run_live_transcription(self) -> None:
        queue = self._live_queue
        if queue is None:
            return

        def on_text(text: str, is_final: bool) -> None:
            if text == self._live_last_text:
                return
            self._live_last_text = text
            label = "FINAL" if is_final else "LIVE"
            print(f"{label} {text}", flush=True)
            LOGGER.info("%s transcription: %s", label.lower(), text)

        result = self.asr_client.transcribe_pcm_live(
            queue,
            sample_rate=self.config.sample_rate,
            on_text=on_text,
        )
        try:
            with self._lock:
                ended_while_recording = self._state == "recording"
            if ended_while_recording:
                LOGGER.warning("live ASR ended before local stop; stopping recorder")
                self.recorder.stop()
                self.recorder.on_chunk = None

            with self._lock:
                cancelled = self._live_cancelled
            if cancelled:
                LOGGER.info("live transcription discarded")
                return

            if not result.ok:
                notify("Voice input", "Transcription failed")
                LOGGER.error("live transcription failed: %s", result.error)
                return

            text = result.text.strip()
            LOGGER.info(
                "live transcription finished: %r latency=%.2fs request_id=%s",
                text,
                result.latency_seconds,
                result.request_id,
            )
            if not text:
                notify("Voice input", "No speech recognized")
                return

            inject_text(text, self.config.inject)
            notify("Voice input", "Text ready")
        finally:
            with self._lock:
                self._state = "idle"
                self._live_queue = None
                self._live_thread = None
                self._live_cancelled = False
                self._stop_requested = False

    def _transcribe_and_inject(self, audio: bytes) -> None:
        notify("Voice input", "Transcribing")
        result = self.asr_client.transcribe_pcm(audio, sample_rate=self.config.sample_rate)
        try:
            if not result.ok:
                notify("Voice input", "Transcription failed")
                LOGGER.error("transcription failed: %s", result.error)
                return

            text = result.text.strip()
            LOGGER.info(
                "transcription finished: %r latency=%.2fs request_id=%s",
                text,
                result.latency_seconds,
                result.request_id,
            )
            if not text:
                notify("Voice input", "No speech recognized")
                return

            inject_text(text, self.config.inject)
            notify("Voice input", "Text ready")
        finally:
            with self._lock:
                self._state = "idle"


def listen_for_hotkey(
    hotkey: str,
    callback: Callable[[], None],
    release_callback: Callable[[], None] | None = None,
) -> None:
    mouse_button = _parse_mouse_button(hotkey)
    if mouse_button is not None:
        listen_for_mouse_button(mouse_button, callback, release_callback)
        return

    try:
        from pynput import keyboard
    except ImportError as exc:
        raise RuntimeError("missing dependency: pip install pynput") from exc

    def wrapped_press() -> None:
        try:
            callback()
        except Exception:
            LOGGER.exception("hotkey callback failed")

    def wrapped_release() -> None:
        if release_callback is None:
            return
        try:
            release_callback()
        except Exception:
            LOGGER.exception("hotkey release callback failed")

    LOGGER.info("listening for hotkey: %s", hotkey)
    if "+" not in hotkey:
        keys = keyboard.HotKey.parse(hotkey)
        if len(keys) != 1:
            raise ValueError(f"invalid single-key hotkey: {hotkey}")
        target_key = keys[0]
        is_down = False

        def on_press(key) -> None:  # noqa: ANN001
            nonlocal is_down
            if _same_key(key, target_key) and not is_down:
                is_down = True
                wrapped_press()

        def on_release(key) -> None:  # noqa: ANN001
            nonlocal is_down
            if _same_key(key, target_key):
                if is_down:
                    wrapped_release()
                is_down = False

        with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
            listener.join()
        return

    if release_callback is not None:
        LOGGER.warning("release callback is only supported for single-key hotkeys")
    with keyboard.GlobalHotKeys({hotkey: wrapped_press}) as listener:
        listener.join()


def _parse_mouse_button(hotkey: str) -> int | None:
    normalized = hotkey.strip().lower().replace("-", "").replace("_", "")
    if not normalized.startswith("mouse"):
        return None
    button = normalized.removeprefix("mouse")
    if not button.isdigit():
        raise ValueError(f"invalid mouse hotkey: {hotkey}")
    value = int(button)
    if value < 1:
        raise ValueError(f"invalid mouse button: {hotkey}")
    return value


def listen_for_mouse_button(
    button: int,
    callback: Callable[[], None],
    release_callback: Callable[[], None] | None = None,
) -> None:
    if os.environ.get("DISPLAY"):
        listen_for_mouse_button_x11(button, callback, release_callback)
        return

    try:
        from pynput import mouse
    except ImportError as exc:
        raise RuntimeError("missing dependency: pip install pynput") from exc

    target = getattr(mouse.Button, f"button{button}", None)
    if target is None:
        raise ValueError(f"pynput does not expose mouse button {button}")

    LOGGER.warning(
        "listening for mouse%d without X11 grab; the application may also receive it",
        button,
    )

    def on_click(x, y, clicked_button, pressed) -> None:  # noqa: ANN001
        if clicked_button != target:
            return
        if pressed:
            callback()
        elif release_callback is not None:
            release_callback()

    with mouse.Listener(on_click=on_click) as listener:
        listener.join()


def listen_for_mouse_button_x11(
    button: int,
    callback: Callable[[], None],
    release_callback: Callable[[], None] | None = None,
) -> None:
    try:
        from Xlib import display
        from Xlib.ext import xinput
    except ImportError as exc:
        raise RuntimeError("missing dependency: pip install python-xlib") from exc

    pointer_id = find_xinput_pointer_id("Logitech M720 Triathlon")
    original_button_map: list[int] | None = None
    if pointer_id is not None:
        original_button_map = get_xinput_button_map(pointer_id)
        disabled_map = original_button_map.copy() if original_button_map is not None else []
        if button <= len(disabled_map):
            disabled_map[button - 1] = 0
            set_xinput_button_map(pointer_id, disabled_map)
            LOGGER.info(
                "disabled default action for mouse button %d on XInput device %d",
                button,
                pointer_id,
            )
        else:
            LOGGER.warning("could not disable default action for mouse button %d", button)
    else:
        LOGGER.warning("could not find Logitech M720 pointer device; default action may leak")

    stop_requested = False

    def restore_and_stop(signum: int, frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True
        restore_xinput_button_map(pointer_id, original_button_map)
        raise SystemExit(0)

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    previous_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, restore_and_stop)
    signal.signal(signal.SIGINT, restore_and_stop)

    dpy = display.Display()
    root = dpy.screen().root
    try:
        root.xinput_select_events(
            [
                (
                    xinput.AllDevices,
                    xinput.RawButtonPressMask | xinput.RawButtonReleaseMask,
                )
            ]
        )
        dpy.flush()

        LOGGER.info("listening for raw mouse button %d on X11", button)
        is_down = False
        while not stop_requested:
            event = dpy.next_event()
            if getattr(event, "evtype", None) not in {
                xinput.RawButtonPress,
                xinput.RawButtonRelease,
            }:
                continue
            detail = _raw_xi_button_detail(getattr(event, "data", b""))
            if detail != button:
                continue
            if event.evtype == xinput.RawButtonPress and not is_down:
                is_down = True
                LOGGER.info("mouse button %d press", button)
                callback()
            elif event.evtype == xinput.RawButtonRelease:
                if is_down and release_callback is not None:
                    LOGGER.info("mouse button %d release", button)
                    release_callback()
                is_down = False
    finally:
        restore_xinput_button_map(pointer_id, original_button_map)
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)
        dpy.close()


def _raw_xi_button_detail(data: bytes) -> int | None:
    if len(data) < 10:
        return None
    return struct.unpack_from("<I", data, 6)[0]


def find_xinput_pointer_id(device_name: str) -> int | None:
    if not shutil.which("xinput"):
        return None
    result = subprocess.run(
        ["xinput", "--list"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if device_name not in line or "slave  pointer" not in line:
            continue
        match = re.search(r"\bid=(\d+)\b", line)
        if match:
            return int(match.group(1))
    return None


def get_xinput_button_map(device_id: int) -> list[int] | None:
    if not shutil.which("xinput"):
        return None
    result = subprocess.run(
        ["xinput", "get-button-map", str(device_id)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        return [int(value) for value in result.stdout.split()]
    except ValueError:
        return None


def set_xinput_button_map(device_id: int, button_map: list[int]) -> None:
    if not shutil.which("xinput"):
        return
    subprocess.run(
        ["xinput", "set-button-map", str(device_id), *map(str, button_map)],
        check=False,
    )


def restore_xinput_button_map(
    device_id: int | None, button_map: list[int] | None
) -> None:
    if device_id is not None and button_map is not None:
        set_xinput_button_map(device_id, button_map)


def listen_for_mouse_button_x11_core_grab(
    button: int,
    callback: Callable[[], None],
    release_callback: Callable[[], None] | None = None,
) -> None:
    try:
        from Xlib import X, display, error
    except ImportError as exc:
        raise RuntimeError("missing dependency: pip install python-xlib") from exc

    dpy = display.Display()
    root = dpy.screen().root
    event_mask = X.ButtonPressMask | X.ButtonReleaseMask
    try:
        root.grab_button(
            button,
            X.AnyModifier,
            False,
            event_mask,
            X.GrabModeAsync,
            X.GrabModeAsync,
            X.NONE,
            X.NONE,
        )
        dpy.sync()
    except error.BadAccess as exc:
        dpy.close()
        raise RuntimeError(
            f"mouse button {button} is already grabbed by another application"
        ) from exc

    LOGGER.info("grabbed mouse button %d on X11", button)
    is_down = False
    try:
        while True:
            event = dpy.next_event()
            if getattr(event, "detail", None) != button:
                continue
            if event.type == X.ButtonPress and not is_down:
                is_down = True
                LOGGER.info("mouse button %d press", button)
                callback()
            elif event.type == X.ButtonRelease:
                if is_down and release_callback is not None:
                    LOGGER.info("mouse button %d release", button)
                    release_callback()
                is_down = False
    finally:
        root.ungrab_button(button, X.AnyModifier)
        dpy.close()


def _same_key(left: object, right: object) -> bool:
    if left == right:
        return True
    left_value = getattr(left, "value", left)
    right_value = getattr(right, "value", right)
    left_vk = getattr(left_value, "vk", None)
    right_vk = getattr(right_value, "vk", None)
    if left_vk is not None and right_vk is not None:
        return left_vk == right_vk
    left_char = getattr(left_value, "char", None)
    right_char = getattr(right_value, "char", None)
    return left_char is not None and left_char == right_char


def save_wav_recording(
    audio: bytes,
    sample_rate: int,
    recordings_dir: str,
    keep: int,
) -> str:
    os.makedirs(recordings_dir, exist_ok=True)
    filename = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}.wav"
    path = os.path.join(recordings_dir, filename)
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(audio)
    update_latest_recording_link(recordings_dir, path)
    prune_recordings(recordings_dir, keep)
    return path


def update_latest_recording_link(recordings_dir: str, path: str) -> None:
    latest_path = os.path.join(recordings_dir, "latest.wav")
    try:
        if os.path.lexists(latest_path):
            os.remove(latest_path)
        os.symlink(os.path.basename(path), latest_path)
    except OSError:
        try:
            shutil.copy2(path, latest_path)
        except OSError:
            LOGGER.debug("failed to update latest recording link", exc_info=True)


def prune_recordings(recordings_dir: str, keep: int) -> None:
    if keep <= 0:
        return
    try:
        entries = [
            os.path.join(recordings_dir, name)
            for name in os.listdir(recordings_dir)
            if name.lower().endswith(".wav") and name != "latest.wav"
        ]
    except OSError:
        return
    entries.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    for path in entries[keep:]:
        try:
            os.remove(path)
        except OSError:
            LOGGER.debug("failed to prune old recording: %s", path, exc_info=True)


def inject_text(text: str, mode: str) -> None:
    if mode == "none":
        print(text, flush=True)
        return

    if mode == "paste":
        if paste_text(text):
            return
        LOGGER.warning("paste backend unavailable")
        print(text, flush=True)
        return

    if mode == "clipboard":
        if copy_to_clipboard(text):
            print(text, flush=True)
            return

    print(text, flush=True)


def paste_text(text: str) -> bool:
    previous = read_clipboard()
    had_previous = previous is not None
    if not copy_to_clipboard(text):
        return False
    if not paste_from_clipboard():
        if had_previous:
            copy_to_clipboard(previous)
        return False
    if _env_bool("VOICE_INPUT_RESTORE_CLIPBOARD", True):
        time.sleep(float(os.environ.get("VOICE_INPUT_RESTORE_DELAY", "0.2")))
        if had_previous:
            copy_to_clipboard(previous)
        else:
            clear_clipboard()
    return True


def read_clipboard() -> str | None:
    system = platform.system()
    if system == "Darwin" and shutil.which("pbpaste"):
        result = subprocess.run(["pbpaste"], check=False, capture_output=True)
        if result.returncode == 0:
            return result.stdout.decode("utf-8", errors="replace")

    if shutil.which("wl-paste") and os.environ.get("WAYLAND_DISPLAY"):
        result = subprocess.run(
            ["wl-paste", "--no-newline"],
            check=False,
            capture_output=True,
        )
        if result.returncode == 0:
            return result.stdout.decode("utf-8", errors="replace")

    if shutil.which("xclip") and os.environ.get("DISPLAY"):
        result = subprocess.run(
            ["xclip", "-selection", "clipboard", "-out"],
            check=False,
            capture_output=True,
        )
        if result.returncode == 0:
            return result.stdout.decode("utf-8", errors="replace")

    if shutil.which("xsel") and os.environ.get("DISPLAY"):
        result = subprocess.run(
            ["xsel", "--clipboard", "--output"],
            check=False,
            capture_output=True,
        )
        if result.returncode == 0:
            return result.stdout.decode("utf-8", errors="replace")

    try:
        import pyperclip

        return pyperclip.paste()
    except Exception:
        LOGGER.debug("clipboard read backend unavailable", exc_info=True)

    return None


def clear_clipboard() -> bool:
    if shutil.which("wl-copy") and os.environ.get("WAYLAND_DISPLAY"):
        result = subprocess.run(["wl-copy", "--clear"], check=False)
        if result.returncode == 0:
            return True

    if shutil.which("xclip") and os.environ.get("DISPLAY"):
        result = subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=b"",
            check=False,
        )
        if result.returncode == 0:
            return True

    try:
        import pyperclip

        pyperclip.copy("")
        return True
    except Exception:
        LOGGER.debug("clipboard clear backend unavailable", exc_info=True)

    return False


def copy_to_clipboard(text: str) -> bool:
    global _CLIPBOARD_ROOT
    system = platform.system()
    if system == "Darwin" and shutil.which("pbcopy"):
        result = subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)
        if result.returncode == 0:
            return True

    if shutil.which("wl-copy") and os.environ.get("WAYLAND_DISPLAY"):
        result = subprocess.run(["wl-copy"], input=text.encode("utf-8"), check=False)
        if result.returncode == 0:
            return True

    if shutil.which("xclip") and os.environ.get("DISPLAY"):
        result = subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=text.encode("utf-8"),
            check=False,
        )
        if result.returncode == 0:
            return True

    if shutil.which("xsel") and os.environ.get("DISPLAY"):
        result = subprocess.run(
            ["xsel", "--clipboard", "--input"],
            input=text.encode("utf-8"),
            check=False,
        )
        if result.returncode == 0:
            return True

    if os.environ.get("DISPLAY"):
        try:
            import tkinter

            if _CLIPBOARD_ROOT is None:
                _CLIPBOARD_ROOT = tkinter.Tk()
                _CLIPBOARD_ROOT.withdraw()
            _CLIPBOARD_ROOT.clipboard_clear()
            _CLIPBOARD_ROOT.clipboard_append(text)
            _CLIPBOARD_ROOT.update()
            return True
        except Exception:
            LOGGER.exception("tkinter clipboard backend failed")

    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except Exception:
        LOGGER.exception("pyperclip clipboard backend failed")
        return False


def paste_from_clipboard() -> bool:
    shortcut = resolve_paste_shortcut()
    system = platform.system()
    if system == "Darwin":
        result = subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to keystroke "v" using command down',
            ],
            check=False,
        )
        return result.returncode == 0

    if shutil.which("xdotool") and os.environ.get("DISPLAY"):
        result = subprocess.run(
            ["xdotool", "key", "--clearmodifiers", shortcut],
            check=False,
        )
        return result.returncode == 0

    if os.environ.get("DISPLAY"):
        return send_x11_paste_shortcut(shortcut)

    return False


def resolve_paste_shortcut() -> str:
    info = active_window_info()
    combined = " ".join(value for value in info.values() if value).lower()
    terminal_hints = {
        "terminal",
        "gnome-terminal",
        "kgx",
        "konsole",
        "xterm",
        "xfce4-terminal",
        "mate-terminal",
        "tilix",
        "terminator",
        "alacritty",
        "kitty",
        "wezterm",
        "urxvt",
        "rxvt",
        "ptyxis",
        "blackbox",
        "tabby",
        "gnome-terminal-server",
        "codex",
        "codexws",
        "mozilla firefox",
        "firefox",
    }
    if any(hint in combined for hint in terminal_hints):
        return "ctrl+shift+v"
    return "ctrl+v"


def active_window_info() -> dict[str, str]:
    if not shutil.which("xdotool") or not os.environ.get("DISPLAY"):
        return {}
    info: dict[str, str] = {}
    window = _run_text(["xdotool", "getactivewindow"]).strip()
    if not window:
        return info
    info["id"] = window
    name = _run_text(["xdotool", "getwindowname", window]).strip()
    if name:
        info["name"] = name
    class_name = _run_text(["xdotool", "getwindowclassname", window]).strip()
    if class_name:
        info["class"] = class_name
    pid = _run_text(["xdotool", "getwindowpid", window]).strip()
    if pid:
        info["pid"] = pid
        process_name = _run_text(["ps", "-p", pid, "-o", "comm="]).strip()
        if process_name:
            info["process"] = process_name
        command = _run_text(["ps", "-p", pid, "-o", "args="]).strip()
        if command:
            info["command"] = command
    return info


def _run_text(cmd: list[str]) -> str:
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except OSError:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout


def send_x11_paste_shortcut(shortcut: str) -> bool:
    try:
        from Xlib import XK, X, display
        from Xlib.ext import xtest
    except ImportError:
        return False

    key_names = {
        "ctrl+v": ["Control_L", "v"],
        "ctrl+shift+v": ["Control_L", "Shift_L", "v"],
    }.get(shortcut)
    if not key_names:
        return False

    dpy = display.Display()
    pressed: list[int] = []
    try:
        for name in key_names:
            keycode = _x11_paste_keycode_for_keysym(dpy, XK, name)
            if not keycode:
                LOGGER.warning("cannot resolve X11 key name: %s", name)
                return False
            xtest.fake_input(dpy, X.KeyPress, keycode)
            pressed.append(keycode)
        for keycode in reversed(pressed):
            xtest.fake_input(dpy, X.KeyRelease, keycode)
        dpy.flush()
        return True
    finally:
        dpy.close()


def _x11_paste_keycode_for_keysym(dpy: object, xk_module: object, name: str) -> int:
    keysym_name = name.lower() if len(name) == 1 else name
    keysym = xk_module.string_to_keysym(keysym_name)
    return dpy.keysym_to_keycode(keysym) if keysym else 0


def notify(title: str, body: str) -> None:
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", title, body], check=False)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def run_once(seconds: float, service: VoiceTypingService) -> None:
    service.start_recording()
    time.sleep(seconds)
    service.stop_recording()
    while True:
        with service._lock:
            if service._state == "idle":
                return
        time.sleep(0.1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local hotkey voice input powered by Doubao/Volcengine ASR"
    )
    parser.add_argument(
        "--hotkey",
        default=os.environ.get("VOICE_INPUT_HOTKEY", "<ctrl_r>"),
        help="pynput hotkey, for example '<ctrl_r>', '<ctrl>+<alt>+<space>', or '<f13>'",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=int(os.environ.get("VOICE_INPUT_SAMPLE_RATE", "16000")),
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=float(os.environ.get("VOICE_INPUT_MAX_SECONDS", "60")),
    )
    parser.add_argument(
        "--min-seconds",
        type=float,
        default=float(os.environ.get("VOICE_INPUT_MIN_SECONDS", "0.25")),
    )
    parser.add_argument(
        "--inject",
        choices=["paste", "clipboard", "none"],
        default=os.environ.get("VOICE_INPUT_INJECT", "paste"),
        help=(
            "'paste' copies recognized text and replays the service paste shortcut; "
            "'clipboard' copies text only; 'none' prints it only"
        ),
    )
    parser.set_defaults(live=_env_bool("VOICE_INPUT_LIVE", True))
    parser.add_argument(
        "--live",
        dest="live",
        action="store_true",
        help="stream microphone audio to ASR while recording",
    )
    parser.add_argument(
        "--no-live",
        dest="live",
        action="store_false",
        help="record locally first, then transcribe after stopping",
    )
    parser.add_argument(
        "--once-seconds",
        type=float,
        help="record once for N seconds, transcribe, then exit",
    )
    parser.set_defaults(save_recordings=_env_bool("VOICE_INPUT_SAVE_RECORDINGS", False))
    parser.add_argument(
        "--save-recordings",
        dest="save_recordings",
        action="store_true",
        help="save each completed recording as a WAV file for diagnostics",
    )
    parser.add_argument(
        "--no-save-recordings",
        dest="save_recordings",
        action="store_false",
        help="do not save completed recordings",
    )
    parser.add_argument(
        "--recordings-dir",
        default=os.environ.get(
            "VOICE_INPUT_RECORDINGS_DIR",
            os.path.join(os.getcwd(), "voice_input", "recordings"),
        ),
        help="directory for saved WAV recordings",
    )
    parser.add_argument(
        "--recordings-keep",
        type=int,
        default=int(os.environ.get("VOICE_INPUT_RECORDINGS_KEEP", "30")),
        help="maximum number of saved WAV recordings to keep",
    )
    parser.add_argument(
        "--trigger-mode",
        choices=["hold", "toggle"],
        default=os.environ.get("VOICE_INPUT_TRIGGER_MODE", "hold"),
        help="'hold' starts on press and stops on release; 'toggle' starts/stops on press",
    )
    parser.add_argument("--debug", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.inject not in {"paste", "clipboard", "none"}:
        parser.error(
            f"unsupported --inject mode from configuration: {args.inject}; "
            "voice input only supports fixed service actions"
        )
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("websockets").setLevel(logging.WARNING)

    service_config = ServiceConfig(
        hotkey=args.hotkey,
        sample_rate=args.sample_rate,
        max_seconds=args.max_seconds,
        min_seconds=args.min_seconds,
        inject=args.inject,
        live=args.live,
        save_recordings=args.save_recordings,
        recordings_dir=args.recordings_dir,
        recordings_keep=args.recordings_keep,
    )
    asr_client = DoubaoASRClient(DoubaoASRConfig.from_env())
    service = VoiceTypingService(service_config, asr_client)

    if args.once_seconds:
        run_once(args.once_seconds, service)
        return 0

    if args.trigger_mode == "hold":
        LOGGER.info("hold %s to record; release it to stop", args.hotkey)
    else:
        LOGGER.info("press %s to start recording; press it again to stop", args.hotkey)
    try:
        if args.trigger_mode == "hold":
            listen_for_hotkey(args.hotkey, service.start_recording, service.stop_recording)
        else:
            listen_for_hotkey(args.hotkey, service.toggle)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
