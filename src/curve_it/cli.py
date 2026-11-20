"""Command-line entry point for snapshots, exports and live monitoring."""

from __future__ import annotations

import argparse
import io
import math
import os
import select
import shutil
import sys
import time
from collections.abc import Callable
from contextlib import redirect_stdout
from pathlib import Path

from .export import save_html, save_png
from .log import find_latest_log, parse_console_log
from .metrics import cn_title, default_panels, format_value
from .terminal import print_ascii_dashboard, print_terminal_dashboard


def watch(render: Callable[[], None], interval: float, interactive: bool) -> None:
    if not interactive:
        while True:
            render()
            sys.stdout.flush()
            time.sleep(interval)

    import termios
    import tty

    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        sys.stdout.write("\033[?1049h\033[?25l")
        while True:
            frame = io.StringIO()
            with redirect_stdout(frame):
                render()
                print(f"{time.strftime('%H:%M:%S')}  |  refresh {interval:g}s  |  q / Ctrl-C to exit")
            sys.stdout.write("\033[H\033[2J" + frame.getvalue())
            sys.stdout.flush()
            until = time.monotonic() + interval
            while (left := until - time.monotonic()) > 0:
                if not select.select([fd], [], [], left)[0]:
                    break
                keys = os.read(fd, 256)
                if not keys or b"q" in keys.lower():
                    return
    finally:
        try:
            sys.stdout.write("\033[?25h\033[?1049l")
            sys.stdout.flush()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, original)


def export_path(option: str | None, source: Path, suffix: str) -> Path | None:
    if option is None:
        return None
    dest = (Path(option).expanduser() if option else Path.cwd() / source.with_suffix(suffix).name).resolve()
    if dest == source or (dest.exists() and dest.samefile(source)):
        raise ValueError(f"export would overwrite the input log: {dest}")
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="curve_it", description="Plot RSL-RL console logs locally.")
    parser.add_argument("source", type=Path, nargs="?", help="log file or directory")
    parser.add_argument("--log", type=Path, help="log file (equivalent to the positional file)")
    parser.add_argument("--pattern", default="be2e_*.log", help="file glob for directory input (default: %(default)s)")
    parser.add_argument("--watch", type=float, default=0, metavar="SECONDS", help="refresh at this interval")
    parser.add_argument("--list", action="store_true", help="list metric keys and their latest values")
    parser.add_argument("--metrics", help="comma-separated metric keys")
    parser.add_argument("--ascii", action="store_true", help="ASCII dashboard for Linux virtual consoles")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI color")
    parser.add_argument("--html", nargs="?", const="", metavar="FILE", help="export standalone HTML")
    parser.add_argument("--png", nargs="?", const="", metavar="FILE", help="export PNG (requires the png extra)")
    parser.add_argument("--smooth", type=float, default=0.5, help="EMA coefficient for exports (0-0.99)")
    parser.add_argument(
        "--term-smooth", "--term_smooth", type=float, default=0, help="EMA coefficient for terminal plots"
    )
    args = parser.parse_args(argv)
    if args.source is not None and args.log is not None:
        parser.error("choose a positional path or --log")
    if not math.isfinite(args.watch) or args.watch < 0:
        parser.error("--watch must be finite and non-negative")
    if not all(0 <= value <= 0.99 for value in (args.smooth, args.term_smooth)):
        parser.error("smoothing coefficients must be between 0 and 0.99")
    if args.list and (args.watch or args.html is not None or args.png is not None):
        parser.error("--list is a one-time view")
    keys = list(dict.fromkeys(key.strip() for key in (args.metrics or "").split(",") if key.strip()))
    if args.metrics is not None and not keys:
        parser.error("--metrics requires at least one key")

    try:
        path = (args.log or args.source or Path("logs/console")).expanduser().resolve()
        path = find_latest_log(path, args.pattern) if path.is_dir() else path
        if not path.is_file():
            raise FileNotFoundError(f"log not found: {path}")
        html_path, png_path = export_path(args.html, path, ".html"), export_path(args.png, path, ".png")
        if html_path is not None and html_path == png_path:
            raise ValueError("HTML and PNG require different output files")

        terminal = os.environ.get("TERM", "dumb")
        interactive = os.name == "posix" and sys.stdin.isatty() and sys.stdout.isatty() and terminal != "dumb"
        ascii_only = args.ascii or terminal in {"linux", "dumb"} or not sys.stdout.isatty()
        color = interactive and not args.no_color and "NO_COLOR" not in os.environ
        last_export = None

        def render() -> None:
            nonlocal last_export
            try:
                stat = path.stat()
                log = parse_console_log(path)
            except FileNotFoundError:
                if not args.watch:
                    raise
                print(f"Waiting for {path}")
                return
            if args.list:
                for key in sorted(log.series):
                    print(f"{key:42} {format_value(log.series[key][-1][1])}")
                return
            panels = [(key, cn_title(key)) for key in keys] if keys else default_panels(log)
            size = shutil.get_terminal_size((108, 40))
            if ascii_only or size.columns < 104 or (args.watch and size.lines < 38):
                print_ascii_dashboard(log, panels, width=size.columns, smooth=args.term_smooth)
            else:
                print_terminal_dashboard(log, panels, smooth=args.term_smooth, color=color)
            stamp = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
            if panels and stamp != last_export:
                if html_path is not None:
                    save_html(log, panels, str(html_path), smooth=args.smooth, refresh_s=math.ceil(args.watch))
                    print(f"HTML: {html_path}")
                if png_path is not None:
                    save_png(log, panels, str(png_path), smooth=args.smooth)
                    print(f"PNG: {png_path}")
                last_export = stamp

        if args.watch:
            watch(render, args.watch, interactive)
        else:
            render()
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, ImportError) as error:
        parser.exit(1, f"curve_it: {error}\n")
    return 0
