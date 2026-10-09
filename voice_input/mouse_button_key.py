from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
import subprocess
from collections.abc import Callable

from .voice_service import listen_for_mouse_button_x11

LOGGER = logging.getLogger(__name__)


class StopListening(Exception):
    pass


def parse_mouse_button(value: str) -> int:
    normalized = value.strip().lower().replace("-", "").replace("_", "")
    if normalized.startswith("mouse"):
        normalized = normalized.removeprefix("mouse")
    if not normalized.isdigit():
        raise argparse.ArgumentTypeError(f"invalid mouse button: {value}")
    button = int(normalized)
    if button < 1:
        raise argparse.ArgumentTypeError(f"invalid mouse button: {value}")
    return button


def send_key(key: str) -> None:
    if not os.environ.get("DISPLAY"):
        raise RuntimeError("DISPLAY is not set; xdotool cannot target an X11 window")
    if not shutil.which("xdotool"):
        raise RuntimeError("missing dependency: xdotool")
    subprocess.run(
        ["xdotool", "key", "--clearmodifiers", key],
        check=True,
    )


def listen_for_mouse_button_x11_grab(button: int, callback: Callable[[], None]) -> None:
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

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    previous_sigint = signal.getsignal(signal.SIGINT)

    def stop(signum: int, frame: object) -> None:
        raise StopListening

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

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
                is_down = False
    except StopListening:
        LOGGER.info("stopping mouse button listener")
    finally:
        root.ungrab_button(button, X.AnyModifier)
        dpy.sync()
        dpy.close()
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Map an X11 mouse button to a key")
    parser.add_argument(
        "--button",
        type=parse_mouse_button,
        default=parse_mouse_button(os.environ.get("MOUSE_BUTTON_KEY_BUTTON", "mouse8")),
        help="mouse button to intercept, for example mouse8",
    )
    parser.add_argument(
        "--key",
        default=os.environ.get("MOUSE_BUTTON_KEY_KEY", "Return"),
        help="xdotool key name to send, for example Return",
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "grab", "raw"],
        default=os.environ.get("MOUSE_BUTTON_KEY_BACKEND", "auto"),
        help="grab blocks the default button action; raw is a fallback",
    )
    parser.add_argument("--debug", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    def callback() -> None:
        try:
            send_key(args.key)
        except Exception:
            LOGGER.exception("failed to send key %s", args.key)

    LOGGER.info("mapping mouse%d to key %s using backend=%s", args.button, args.key, args.backend)
    if args.backend in {"auto", "grab"}:
        try:
            listen_for_mouse_button_x11_grab(args.button, callback)
            return 0
        except RuntimeError:
            if args.backend == "grab":
                raise
            LOGGER.exception("X11 grab backend failed; falling back to raw XInput")

    listen_for_mouse_button_x11(args.button, callback)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
