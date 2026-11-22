"""Command-line entry point for snapshots, exports and live monitoring."""

from __future__ import annotations

import argparse
import math
import os
import shutil
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from .annotations import load_annotations, validate_annotation_keys
from .dashboard import Panel
from .export import save_html, save_png
from .hardware import HardwareMonitor, HardwareSnapshot
from .live import watch
from .log import LogReader, ParsedLog, find_latest_log
from .metrics import format_value
from .notifications import notifier_from_env
from .profiles import PROFILES, select_panels
from .reports import ReportSchedule, parse_interval
from .terminal import DashboardFrame, render_dashboard


@dataclass(frozen=True, eq=False)
class _DashboardData:
    log: ParsedLog
    panels: list[Panel]
    title: str
    hardware: HardwareSnapshot | None = None
    notification_status: str = ""


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
    parser.add_argument("--profile", choices=("auto", *PROFILES), default="auto", help="dashboard preset")
    parser.add_argument("--pin-log", action="store_true", help="keep the first log selected in directory watch mode")
    parser.add_argument("--watch", type=float, default=0, metavar="SECONDS", help="refresh at this interval")
    parser.add_argument("--notify-every", metavar="DURATION", help="send training reports, e.g. 10m or 30m")
    parser.add_argument("--list", action="store_true", help="list metric keys and their latest values")
    metrics = parser.add_mutually_exclusive_group()
    metrics.add_argument("--metrics", help="comma-separated metric keys")
    metrics.add_argument("--all-metrics", action="store_true", help="show every available metric in the terminal")
    parser.add_argument("--annotations", type=Path, metavar="JSON", help="labels and reference ranges by metric key")
    parser.add_argument("--layout", choices=("dense", "classic"), help="snapshot layout (default: classic)")
    parser.add_argument(
        "--history",
        type=int,
        metavar="ITERATIONS",
        help="iterations to show in terminal charts (watch defaults to 120, 0 uses the full run)",
    )
    charset = parser.add_mutually_exclusive_group()
    charset.add_argument("--ascii", action="store_true", help="ASCII dashboard for Linux virtual consoles")
    charset.add_argument("--unicode", action="store_true", help="use Unicode Braille when supported by your font")
    parser.add_argument("--no-hardware", action="store_true", help="hide local hardware readouts")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI color")
    parser.add_argument("--html", nargs="?", const="", metavar="FILE", help="export standalone HTML")
    parser.add_argument("--png", nargs="?", const="", metavar="FILE", help="export PNG (requires the png extra)")
    parser.add_argument("--smooth", type=float, default=0.5, help="EMA coefficient for exports (0-0.99)")
    parser.add_argument(
        "--term-smooth", "--term_smooth", type=float, default=0, help="EMA coefficient for terminal plots"
    )
    args = parser.parse_args(argv)
    if args.history is not None and args.history < 0:
        parser.error("--history must be non-negative")
    if args.source is not None and args.log is not None:
        parser.error("choose a positional path or --log")
    if not math.isfinite(args.watch) or args.watch < 0:
        parser.error("--watch must be finite and non-negative")
    if args.notify_every is not None and not args.watch:
        args.watch = 5
    if not all(0 <= value <= 0.99 for value in (args.smooth, args.term_smooth)):
        parser.error("smoothing coefficients must be between 0 and 0.99")
    if args.list and (args.watch or args.html is not None or args.png is not None):
        parser.error("--list is a one-time view")
    keys = list(dict.fromkeys(key.strip() for key in (args.metrics or "").split(",") if key.strip()))
    if args.metrics is not None and not keys:
        parser.error("--metrics requires at least one key")

    schedule = None
    try:
        annotations = load_annotations(args.annotations) if args.annotations else {}
        if args.notify_every is not None:
            report_interval = parse_interval(args.notify_every)
            schedule = ReportSchedule(
                notifier_from_env(), report_interval, chart_url=os.environ.get("CURVE_IT_CHART_URL", "").strip()
            )
        source = (args.log or args.source or Path("logs/console")).expanduser().resolve()
        directory = source.is_dir()
        terminal = os.environ.get("TERM", "dumb")
        interactive = os.name == "posix" and sys.stdin.isatty() and sys.stdout.isatty() and terminal != "dumb"
        ascii_only = not args.unicode and (args.ascii or terminal in {"linux", "dumb"} or not sys.stdout.isatty())
        dense_default = args.layout == "dense"
        live_view = bool(args.watch and interactive)
        color = interactive and not args.no_color and "NO_COLOR" not in os.environ
        reader: LogReader | None = None
        initial_log = ParsedLog(str(source))
        initial_profile, _ = select_panels(initial_log, profile=args.profile)
        initial_data = current_data = _DashboardData(initial_log, [], initial_profile.title)
        last_revision = None
        monitor = None if args.list or args.no_hardware else HardwareMonitor()
        last_html = last_png = None
        export_names: list[Path] = []
        cached_frame: DashboardFrame | None = None
        last_frame = None

        def refresh() -> _DashboardData:
            nonlocal reader, last_html, last_png, last_revision, current_data
            try:
                path = source
                if directory:
                    path = reader.path if reader is not None and args.pin_log else find_latest_log(source, args.pattern)
                if reader is None or reader.path != path:
                    reader = LogReader(path)
                log = reader.read()
                revision = (reader, reader.revision)
            except FileNotFoundError:
                if not args.watch:
                    raise
                log = ParsedLog(str(reader.path if reader is not None and args.pin_log else source))
                revision = None
            if args.list:
                return replace(current_data, log=log)
            if annotations and not args.watch:
                validate_annotation_keys(annotations, log.series)

            hardware = monitor.read() if monitor is not None else None
            profile, panels = select_panels(log, profile=args.profile, keys=keys or None)
            if annotations and not keys:
                known_keys = list(
                    dict.fromkeys([panel.key for panel in panels] + [key for key in annotations if key in log.series])
                )
                profile, panels = select_panels(log, profile=args.profile, keys=known_keys)
            export_panels = [
                replace(panel, annotations=annotations[panel.key]) if panel.key in annotations else panel
                for panel in panels
            ]
            html_revision = (revision, hardware)
            update_html = args.html is not None and html_revision != last_html
            update_png = args.png is not None and revision != last_png
            if revision is not None and panels and (update_html or update_png):
                path = Path(log.path)
                html_path = export_path(args.html, path, ".html")
                png_path = export_path(args.png, path, ".png")
                if html_path is not None and html_path == png_path:
                    raise ValueError("HTML and PNG require different output files")
                # A directory watcher can switch sources. Validate each destination against the current input.
                if update_html and html_path is not None:
                    save_html(
                        log,
                        export_panels,
                        str(html_path),
                        smooth=args.smooth,
                        refresh_s=math.ceil(args.watch),
                        title=profile.title,
                        hardware=hardware,
                    )
                    last_html = html_revision
                    if not args.watch:
                        export_names.append(html_path)
                if update_png and png_path is not None:
                    save_png(
                        log, export_panels, str(png_path), smooth=args.smooth, title=profile.title, hardware=hardware
                    )
                    last_png = revision
                    if not args.watch:
                        export_names.append(png_path)
            if schedule is not None:
                schedule.update(log, export_panels if revision is not None else (), profile.title)
            notification_status = schedule.error if schedule is not None else ""
            if revision != last_revision or log.path != current_data.log.path:
                display_panels = export_panels if revision is not None else []
                dense_selection = live_view or dense_default
                if revision is not None and (dense_selection or args.all_metrics):
                    _, display_panels = select_panels(
                        log,
                        profile=args.profile,
                        keys=keys or None,
                        dense=dense_selection,
                        all_metrics=args.all_metrics,
                    )
                    if annotations and not keys:
                        displayed_keys = {panel.key for panel in display_panels}
                        display_panels.extend(panel for panel in export_panels if panel.key not in displayed_keys)
                    display_panels = [
                        replace(panel, annotations=annotations[panel.key]) if panel.key in annotations else panel
                        for panel in display_panels
                    ]
                # The reader appends to its lists on the next refresh. Publish detached lists to the UI.
                if live_view:
                    log = replace(
                        log, iters=log.iters.copy(), series={key: points.copy() for key, points in log.series.items()}
                    )
                current_data = _DashboardData(log, display_panels, profile.title, hardware, notification_status)
                last_revision = revision
            elif hardware != current_data.hardware or notification_status != current_data.notification_status:
                current_data = replace(current_data, hardware=hardware, notification_status=notification_status)
            return current_data

        def render(
            data: _DashboardData,
            size: os.terminal_size,
            *,
            page: int = 0,
            paused: bool = False,
            viewport: bool = False,
            selected_key: str | None = None,
            focused: bool = False,
        ) -> DashboardFrame:
            nonlocal cached_frame, last_frame
            log = data.log
            if args.list:
                return DashboardFrame(
                    tuple(f"{key:42} {format_value(log.series[key][-1][1])}" for key in sorted(log.series)), 0, 1
                )
            dense = dense_default and not viewport
            frame_key = (data, size, page, paused, viewport, selected_key, focused)
            if frame_key == last_frame and cached_frame is not None:
                return cached_frame
            cached_frame = render_dashboard(
                log,
                data.panels,
                data.title,
                width=size.columns,
                height=size.lines if viewport else None,
                smooth=args.term_smooth,
                ascii_only=ascii_only,
                color=color,
                page=page,
                watching=viewport,
                paused=paused,
                hardware=data.hardware,
                dense=dense,
                history=args.history if args.history is not None else (120 if args.watch or dense else 0),
                can_switch_layout=viewport,
                selected_key=selected_key,
                focused=focused,
                notification_status=data.notification_status,
            )
            last_frame = frame_key
            return cached_frame

        if args.watch:
            watch(refresh, render, args.watch, interactive, initial_data)
        else:
            frame = render(refresh(), shutil.get_terminal_size((108, 40)))
            print("\n".join(frame.lines))
            for destination in export_names:
                print(f"{destination.suffix[1:].upper()}: {destination}")
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, ImportError) as error:
        parser.exit(1, f"curve_it: {error}\n")
    finally:
        if schedule is not None:
            schedule.close()
            if schedule.error:
                print(f"curve_it: {schedule.error}", file=sys.stderr)
    return 0
