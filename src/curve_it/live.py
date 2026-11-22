"""Terminal refresh loop and line-by-line drawing for live sessions."""

from __future__ import annotations

import os
import re
import select
import shutil
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from .terminal import DashboardFrame

_CURSOR_KEYS = {b"A": "up", b"B": "down", b"C": "right", b"D": "left"}
_ESCAPE_SEQUENCE = re.compile(rb"\x1b(?:\[[\x20-\x3f]*[\x40-\x7e]|O[\x40-\x7e])")
_ESCAPE_WAIT = 0.06
_Data = TypeVar("_Data")


def screen_update(previous: tuple[str, ...], current: tuple[str, ...]) -> str:
    """Generate terminal writes for the rows whose content changed."""
    writes = []
    for index in range(max(len(previous), len(current))):
        before = previous[index] if index < len(previous) else None
        after = current[index] if index < len(current) else None
        if before != after:
            writes.append(f"\033[{index + 1};1H\033[2K{after or ''}")
    return "".join(writes)


def _read_key(fd: int, pending: bytearray, wait: float) -> str | None:
    """Read one key, retaining following keystrokes for the next redraw."""
    if not pending:
        if not select.select([fd], [], [], wait)[0]:
            return None
        pending.extend(os.read(fd, 256))
        if not pending:
            return "eof"
    if pending[0] != 27:
        key = chr(pending[0]).lower()
        del pending[0]
        return key

    deadline = time.monotonic() + _ESCAPE_WAIT
    while True:
        if match := _ESCAPE_SEQUENCE.match(pending):
            sequence = match.group()
            del pending[: match.end()]
            return _CURSOR_KEYS.get(sequence[-1:], "") if len(sequence) == 3 else ""
        if len(pending) > 1 and pending[1] not in b"[O":
            del pending[0]
            return "escape"
        remaining = max(0.0, deadline - time.monotonic())
        data = os.read(fd, 256) if remaining and select.select([fd], [], [], remaining)[0] else b""
        if not data:
            key = "escape" if len(pending) == 1 else ""
            pending.clear()
            return key
        pending.extend(data)


def watch(
    refresh: Callable[[], _Data],
    render: Callable[..., DashboardFrame],
    interval: float,
    interactive: bool,
    initial: _Data,
) -> None:
    if not interactive:
        previous = None
        while True:
            size = shutil.get_terminal_size((108, 40))
            frame = render(refresh(), size)
            if frame.lines != previous:
                print("\n".join(frame.lines), flush=True)
                previous = frame.lines
            time.sleep(interval)

    import termios
    import tty

    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="curve-refresh")
    try:
        tty.setcbreak(fd)
        sys.stdout.write("\033[?1049h\033[?25l\033[2J")
        sys.stdout.flush()
        page, paused, redraw, force = 0, False, True, True
        focused = False
        selected_key = None
        pending = bytearray()
        previous: tuple[str, ...] = ()
        last_size = None
        next_tick = 0.0
        data = latest = initial
        refresh_task = None
        refresh_while_paused = False
        while True:
            now = time.monotonic()
            if refresh_task is not None and refresh_task.done():
                latest = refresh_task.result()
                refresh_task = None
                next_tick = now + interval
                if not paused or refresh_while_paused:
                    redraw = redraw or data is not latest
                    data = latest
            if refresh_task is None and (force or (not paused and now >= next_tick)):
                refresh_task = executor.submit(refresh)
                refresh_while_paused = paused
                force = False

            size = shutil.get_terminal_size((108, 40))
            resized = size != last_size
            if redraw or resized:
                frame = render(
                    data,
                    size,
                    page=page,
                    paused=paused,
                    viewport=True,
                    selected_key=selected_key,
                    focused=focused,
                )
                if resized:
                    sys.stdout.write("\033[2J")
                    previous = ()
                if diff := screen_update(previous, frame.lines):
                    sys.stdout.write(diff)
                sys.stdout.flush()
                page, previous, last_size = frame.page, frame.lines, size
                selected_key = frame.selected_key
                redraw = False
            wait = 0.25 if paused else min(0.25, max(0.0, next_tick - time.monotonic()))
            if refresh_task is not None:
                wait = 0.05
            key = _read_key(fd, pending, wait)
            if key is None:
                continue
            if key in {"eof", "q"}:
                return
            if key == " ":
                paused = not paused
                redraw = True
                force = not paused
                if not paused:
                    data = latest
            elif key in ("\n", "\r") and selected_key is not None:
                focused, redraw = not focused, True
            elif key == "escape" and focused:
                focused, redraw = False, True
            elif key == "r":
                force = True
            elif key in {"left", "right"} or (focused and key in {"up", "down"}):
                target = page + (1 if key in {"right", "down"} else -1)
                if 0 <= target < frame.pages:
                    page, selected_key, redraw = target, None, True
            elif key in {"up", "down"} and selected_key in frame.keys:
                index = frame.keys.index(selected_key) + (1 if key == "down" else -1)
                if 0 <= index < len(frame.keys):
                    selected_key, redraw = frame.keys[index], True
    finally:
        try:
            sys.stdout.write("\033[?25h\033[?1049l")
            sys.stdout.flush()
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, original)
            finally:
                executor.shutdown(cancel_futures=True)
