"""Terminal charts with Unicode and ASCII rendering."""

from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path
from unicodedata import combining, east_asian_width

from .annotations import Annotation, PointLabel, ReferenceBand, annotation_values, visible_annotations
from .dashboard import Panel, PanelSpecs, fit_dense, fit_live, fit_terminal, panel_specs
from .hardware import HardwareSnapshot, hardware_meters
from .log import ParsedLog
from .metrics import diagnose_health, ema_smooth, format_value
from .profiles import detect_profile

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Unicode Braille 2x4 sub-pixel bit layout:
#   col 0: bits 0,1,2,6 (rows 0..3)
#   col 1: bits 3,4,5,7 (rows 0..3)
_BRAILLE_BITS = (
    (0x01, 0x08),
    (0x02, 0x10),
    (0x04, 0x20),
    (0x40, 0x80),
)


# ANSI 256-color palette matching the dark PNG theme
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[38;5;242m"
_BORDER = "\033[38;5;60m"
_WHITE = "\033[1;97m"
_GREEN = "\033[38;5;114m"
_RED = "\033[38;5;203m"
_YELLOW = "\033[38;5;221m"
_CYAN = "\033[38;5;81m"
_PANEL_COLORS = (
    "\033[38;5;81m",  # 亮青（总回报）
    "\033[38;5;114m",  # 翠绿（进度）
    "\033[38;5;215m",  # 琥珀橙（速度跟踪）
    "\033[38;5;203m",  # 珊瑚红（跌倒率）
    "\033[38;5;177m",  # 丁香紫（抬脚腾空）
    "\033[38;5;80m",  # 青绿（存活步数）
)


def _paint(text: str, code: str, color: bool) -> str:
    return f"{code}{text}{_RESET}" if color else text


def _vis_width(text: str) -> int:
    clean = _ANSI_RE.sub("", text)
    return sum(2 if east_asian_width(ch) in {"F", "W"} else 1 for ch in clean if not combining(ch))


def _plot_rows(
    points: list[tuple[int, float]],
    width: int,
    height: int,
    x_min: int,
    x_max: int,
    y_min: float,
    y_max: float,
    *,
    ascii_only: bool = False,
) -> list[str]:
    pw = width if ascii_only else width * 2
    ph = height if ascii_only else height * 4
    grid = [[0] * width for _ in range(height)]

    def plot_px(px: int, py: int) -> None:
        if 0 <= px < pw and 0 <= py < ph:
            if ascii_only:
                grid[py][px] = 1
            else:
                cx, subx = divmod(px, 2)
                cy, suby = divmod(py, 4)
                grid[cy][cx] |= _BRAILLE_BITS[suby][subx]

    # Join successive subpixel coordinates. Repeated pixels need no interpolation.
    prev_px: int | None = None
    prev_py: int | None = None
    for x, y in points:
        px = int(round((x - x_min) / (x_max - x_min) * (pw - 1)))
        py = int(round((1.0 - (y - y_min) / (y_max - y_min)) * (ph - 1)))
        if px == prev_px and py == prev_py:
            continue
        if prev_px is not None and prev_py is not None:
            steps = max(abs(px - prev_px), abs(py - prev_py), 1)
            for s in range(steps + 1):
                ix = int(round(prev_px + (px - prev_px) * s / steps))
                iy = int(round(prev_py + (py - prev_py) * s / steps))
                plot_px(ix, iy)
        else:
            plot_px(px, py)
        prev_px, prev_py = px, py

    if ascii_only:
        return ["".join("*" if cell else " " for cell in row) for row in grid]
    return ["".join(chr(0x2800 + cell) for cell in row) for row in grid]


def render_braille_box(
    pts: list[tuple[int, float]],
    title: str,
    width: int = 38,
    height: int = 7,
    smooth: float = 0.0,
    *,
    color: bool = True,
    line_color: str = _CYAN,
    lower_is_better: bool = False,
    panel: Panel | None = None,
    ascii_only: bool = False,
    window: tuple[int, int] | None = None,
) -> list[str]:
    """Render a line chart with labeled axes in Braille or ASCII."""
    if not pts:
        return [f" {_paint(title, _WHITE, color)}: {_paint('(暂无数据)', _DIM, color)}"]
    xs = [p[0] for p in pts]
    ys_raw = [p[1] for p in pts]
    ys = ema_smooth(ys_raw, smooth) if smooth > 0.0 else ys_raw

    x_min, x_max = window or (min(xs), max(xs))
    annotations = visible_annotations(panel.annotations, x_min, x_max) if panel else ()
    values = ys + annotation_values(annotations)
    y_min, y_max = min(values), max(values)
    if abs(y_max - y_min) < 1e-9:
        if panel is not None and panel.unit == "%" and 0 <= y_min <= 1:
            y_min, y_max = max(0.0, y_min - 0.025), min(1.0, y_max + 0.025)
        else:
            pad = max(abs(y_max) * 0.1, 0.1)
            y_min -= pad
            y_max += pad
    grid = _plot_rows(
        list(zip(xs, ys)), width, height, x_min, max(x_min + 1, x_max), y_min, y_max, ascii_only=ascii_only
    )

    guide_rows = _annotation_rows(
        annotations, grid, x_min, x_max, y_min, y_max, ascii_only=ascii_only, color=color, line_color=line_color
    )
    cur_v = ys_raw[-1]
    delta = ys_raw[-1] - ys_raw[0]
    sign = "+" if delta >= 0 else ""
    improved = (delta <= 0) if lower_is_better else (delta >= 0)
    delta_col = _GREEN if improved else _RED
    dot = _paint("*" if ascii_only else "●", line_color, color)
    header = (
        f" {dot} {_paint(title, _WHITE, color)}  "
        f"{_paint(format_value(cur_v), _BOLD + line_color, color)} "
        f"{_paint(f'({sign}{format_value(delta)})', delta_col, color)}"
    )
    axis = format_value
    if panel is not None:
        axis = (lambda value: f"{value:.1%}") if panel.unit == "%" else format_value
        current = panel.format(cur_v)
        if ascii_only:
            current = current.replace("°", " deg")
        suffix = ""
        if panel.direction and len(pts) > 1 and width >= 42:
            change = f"{delta * 100:+.1f} pp" if panel.unit == "%" else f"{sign}{format_value(delta)}"
            suffix = f"  ({change})"
        visible_title = _clip(title, width + 10 - _vis_width(current + suffix) - 5, ">" if ascii_only else "…")
        delta_col = _GREEN if delta * panel.direction >= 0 else _RED
        header = (
            f" {dot} {_paint(visible_title, _WHITE, color)}  {_paint(current, _BOLD + line_color, color)}"
            + _paint(suffix, delta_col, color)
        )
    top, vertical, bottom, horizontal = ("+", "|", "+", "-") if ascii_only else ("┐", "│", "└", "─")
    lines = [header]
    for index, row in enumerate(grid):
        axis_label = ""
        edge = vertical
        if index == 0:
            axis_label, edge = axis(y_max), top
        elif index == height - 1:
            axis_label, edge = axis(y_min), bottom
        elif index == height // 2:
            value = y_max - index * (y_max - y_min) / max(1, height - 1)
            axis_label = axis(value)
        label = f"{axis_label:>8} {edge}"
        trace = guide_rows[index] if guide_rows else _paint(row, line_color, color)
        lines.append(_paint(label, _DIM, color) + trace)
    first, last = str(x_min), str(x_max)
    x_axis = first
    if x_min != x_max:
        x_axis += horizontal * max(1, width - len(first) - len(last)) + last
    lines.append(_paint(" " * 10 + x_axis, _DIM, color))
    return lines


def _annotation_rows(
    annotations: tuple[Annotation, ...],
    grid: list[str],
    x_min: int,
    x_max: int,
    y_min: float,
    y_max: float,
    *,
    ascii_only: bool,
    color: bool,
    line_color: str,
) -> list[str]:
    if not annotations:
        return []
    height, width = len(grid), len(grid[0])
    rules: set[int] = set()
    labels: dict[int, tuple[int, str]] = {}
    points: dict[int, set[int]] = {}
    dash, marker, mark = ("-", "@", ">") if ascii_only else ("┄", "◆", "…")

    def y_row(value: float) -> int:
        return round((y_max - value) / (y_max - y_min) * (height - 1))

    for item in annotations:
        label = _clip(_visible_text(item.label, ascii_only=ascii_only), width, mark)
        column = width - _vis_width(label)
        if isinstance(item, ReferenceBand):
            upper, lower = y_row(item.upper), y_row(item.lower)
            rules.update((upper, lower))
            row = (upper + lower) // 2
        else:
            row = y_row(item.value)
            if isinstance(item, PointLabel):
                x = round((item.iteration - x_min) / max(1, x_max - x_min) * (width - 1))
                points.setdefault(row, set()).add(x)
                if x + 2 + _vis_width(label) <= width:
                    column = x + 2
                else:
                    column = max(0, x - _vis_width(label) - 1)
            else:
                rules.add(row)
        # Nearby guides can quantize to the same row. Keep their labels separate when space allows.
        if len(labels) < height:
            row = min((i for i in range(height) if i not in labels), key=lambda i: abs(i - row))
            labels[row] = (column, label)

    output = []
    for index, row in enumerate(grid):
        cells = [
            _paint(char, line_color, color)
            if char not in {" ", "⠀"}
            else (_paint(dash, _DIM, color) if index in rules else " ")
            for char in row
        ]
        for x in points.get(index, ()):
            cells[x] = _paint(marker, _YELLOW, color)
        if index in labels:
            x, label = labels[index]
            cells[x : x + _vis_width(label)] = [_paint(label, _YELLOW, color)]
        output.append("".join(cells))
    return output


def print_terminal_dashboard(
    log: ParsedLog,
    panels: PanelSpecs,
    smooth: float = 0.0,
    *,
    color: bool = True,
) -> None:
    frame = render_dashboard(
        log, panel_specs(panels), detect_profile(log).title, width=104, height=None, smooth=smooth, color=color
    )
    print("\n".join(frame.lines))


def print_ascii_dashboard(log: ParsedLog, panels: PanelSpecs, width: int = 80, smooth: float = 0.0) -> None:
    frame = render_dashboard(log, panel_specs(panels), "", width=width, height=None, smooth=smooth, ascii_only=True)
    print("\n".join(frame.lines))


@dataclass(frozen=True)
class DashboardFrame:
    lines: tuple[str, ...]
    page: int
    pages: int
    keys: tuple[str, ...] = ()
    selected_key: str | None = None


def _visible_text(text: str, *, ascii_only: bool = False) -> str:
    if ascii_only:
        text = text.encode("ascii", errors="backslashreplace").decode("ascii")
    return "".join(char if char.isprintable() else "?" for char in text)


def _clip(text: str, width: int, mark: str = "…") -> str:
    if width <= 0:
        return ""
    if _vis_width(text) <= width:
        return text
    used = len(mark)
    end = 0
    for end, char in enumerate(text):
        used += _vis_width(char)
        if used > width:
            return text[:end] + mark
    return text


def _compact_chart(log: ParsedLog, panel: Panel, width: int, smooth: float, *, ascii_only: bool, color: str) -> str:
    points = log.series.get(panel.key, ())
    label = _visible_text(panel.key if ascii_only else panel.title, ascii_only=ascii_only)
    end = panel.format(points[-1][1]) if points else "--"
    if ascii_only:
        end = end.replace("°", " deg")
    mark = ">" if ascii_only else "…"
    if width <= _vis_width(end) + 1:
        return _clip(end, width, mark)
    if width < 30:
        return _clip(label, width - len(end) - 1, mark) + " " + _clip(end, width - 1, mark)
    name_width = min(42, width // 2)
    label = _clip(label, name_width, mark)
    prefix = label + " " * (name_width - _vis_width(label) + 1) + f"{end:>10}"
    count = min(width - _vis_width(prefix) - 2, len(points))
    if count <= 0:
        return prefix
    values = ema_smooth([value for _, value in points], smooth)
    lo, hi = min(values), max(values)
    glyphs = "._-~=+*#" if ascii_only else "▁▂▃▄▅▆▇█"
    samples = [values[round(i * (len(values) - 1) / max(1, count - 1))] for i in range(count)]
    chart = "".join(glyphs[round((v - lo) / (hi - lo) * 7)] if hi > lo else glyphs[3] for v in samples)
    return prefix + "  " + (_paint(chart, color, True) if color else chart)


def _ascii_trace(
    points: list[tuple[int, float]],
    width: int,
    start: int,
    stop: int,
    lo: float,
    hi: float,
) -> str:
    # Keep the most extreme point in each character when several iterations share a column.
    slots: dict[int, float] = {}
    center = (lo + hi) / 2
    for iteration, value in points:
        column = round((iteration - start) * (width - 1) / max(1, stop - start))
        if column not in slots or abs(value - center) > abs(slots[column] - center):
            slots[column] = value
    glyphs = "._-~=+*#"
    pixels = [" "] * width
    for column, value in slots.items():
        pixels[column] = glyphs[round((value - lo) / (hi - lo) * 7)] if hi > lo else "-"
    return "".join(pixels)


def _dense_chart(
    log: ParsedLog,
    panel: Panel,
    width: int,
    plot_height: int,
    window: tuple[int, int],
    smooth: float,
    *,
    ascii_only: bool,
    color: bool,
    line_color: str,
) -> list[str]:
    raw = log.series.get(panel.key, [])
    start, stop = window
    mark = ">" if ascii_only else "…"
    label = _visible_text(panel.key if ascii_only else panel.title, ascii_only=ascii_only)
    if ascii_only and "/" in label:
        label = label.split("/", 1)[1]
    value = panel.format(raw[-1][1]) if raw else "--"
    if ascii_only:
        value = value.replace("°", " deg")
    value = _clip(value, width, mark)
    if width < 8:
        return [value]
    points = raw[bisect_left(raw, (start,)) :] if raw else []
    if smooth and points:
        points = list(zip((iteration for iteration, _ in points), ema_smooth([v for _, v in points], smooth)))
    lo = min((v for _, v in points), default=0.0)
    hi = max((v for _, v in points), default=1.0)

    if plot_height:
        title = _clip(label, width - _vis_width(value) - 2, mark)
        first = title + " " * (width - _vis_width(title) - _vis_width(value)) + _paint(value, line_color, color)
        if not points:
            return [first] + ["" for _ in range(plot_height)]
        if ascii_only:
            return [first, _ascii_trace(points, width, start, stop, lo, hi)]
        if lo == hi:
            lo, hi = lo - 0.5, hi + 0.5
        return [first] + [
            _paint(row, line_color, color)
            for row in _plot_rows(points, width, plot_height, start, max(start + 1, stop), lo, hi)
        ]

    # A one-line fallback still uses the full time window and retains extrema within each column.
    trace_width = max(0, (width - _vis_width(value) - 4) // 3)
    title = _clip(label, width - _vis_width(value) - trace_width - 4, mark)
    prefix = title + " " * max(0, width - trace_width - 2 - _vis_width(value) - _vis_width(title))
    label_and_value = prefix + _paint(value, line_color, color)
    if not trace_width or not points:
        return [label_and_value]
    if ascii_only:
        trace = _ascii_trace(points, trace_width, start, stop, lo, hi)
    else:
        if lo == hi:
            lo, hi = lo - 0.5, hi + 0.5
        trace = _plot_rows(points, trace_width, 1, start, max(start + 1, stop), lo, hi)[0]
    return [label_and_value + "  " + _paint(trace, line_color, color)]


def render_hardware_panel(
    hardware: HardwareSnapshot,
    width: int,
    *,
    ascii_only: bool = False,
    color: bool = False,
    compact: bool = False,
) -> list[str]:
    """Draw all hardware bars against the same full-scale track."""
    mark = ">" if ascii_only else "…"
    heading = "Hardware " if ascii_only else "硬件 "
    rule = "-" if ascii_only else "─"
    lines = (
        []
        if compact
        else [_paint(_clip(heading + rule * max(0, width - _vis_width(heading)), width, mark), _BORDER, color)]
    )
    meters = hardware_meters(hardware, ascii_only=ascii_only)
    label_width = max(_vis_width(meter.label) for meter in meters)
    # Keep capacity notes legible in a narrow terminal. A wide terminal can pair the GPU bars.
    bar_width = max(1, min(24, (width - label_width - 4) // 3))
    paired = width >= 96 and len(meters) > 3
    column_width = (width - 3) // 2 if paired else width
    if paired:
        bar_width = min(bar_width, (column_width - label_width - 3) // 2)

    def render(index: int, line_width: int) -> str:
        meter = meters[index]
        label = meter.label + " " * (label_width - _vis_width(meter.label))
        value = meter.fraction
        if value is None:
            bar = _paint("?" * bar_width, _DIM, color)
        else:
            levels = 2 if ascii_only else 8
            # Use partial cells to retain low but nonzero load. Reserve the full track for full load.
            limit = bar_width * levels
            scaled = round(value * limit)
            scaled = min(limit - 1, max(1, scaled)) if 0 < value < 1 else scaled
            full, partial = divmod(scaled, levels)
            filled = ("#" if ascii_only else "█") * full
            if partial:
                filled += "+" if ascii_only else "▏▎▍▌▋▊▉"[partial - 1]
            empty = ("-" if ascii_only else "░") * (bar_width - len(filled))
            ansi = {"#66c7ed": _CYAN, "#7edcbb": _GREEN}.get(meter.color, _PANEL_COLORS[4])
            bar = _paint(filled, ansi, color) + _paint(empty, _DIM, color)
        prefix = f"{label} [{bar}]"
        room = line_width - _vis_width(prefix)
        if room <= 0:
            return _clip(_ANSI_RE.sub("", prefix), line_width, mark)
        detail = _clip(_visible_text(meter.detail, ascii_only=ascii_only), room - 2, mark)
        return prefix + ("  " + detail if detail else "")

    # CPU and RAM retain their full-width topology and capacity notes.
    lines.extend(render(index, width) for index in range(2))
    index = 2
    while index < len(meters):
        if paired and index + 1 < len(meters):
            left = render(index, column_width)
            right = render(index + 1, column_width)
            lines.append(left + " " * (column_width + 3 - _vis_width(left)) + right)
            index += 2
        else:
            lines.append(render(index, width))
            index += 1
    return lines


def render_dashboard(
    log: ParsedLog,
    panels: list[Panel],
    title: str,
    *,
    width: int,
    height: int | None,
    smooth: float = 0.0,
    ascii_only: bool = False,
    color: bool = False,
    page: int = 0,
    watching: bool = False,
    paused: bool = False,
    hardware: HardwareSnapshot | None = None,
    dense: bool = False,
    history: int = 120,
    can_switch_layout: bool = False,
    selected_key: str | None = None,
    focused: bool = False,
    notification_status: str = "",
) -> DashboardFrame:
    """Render a viewport without terminal writes, for both snapshots and live sessions."""
    width = max(1, width)
    live_view = can_switch_layout and height is not None
    dense = dense and not live_view
    footer_rows = int(watching)
    header_limit = None
    if live_view:
        grid_rows = fit_live(width, 0, focused=focused)
        chart_rows = grid_rows.page_size // grid_rows.columns
        chart_budget = min(13, max(chart_rows, height - 4)) if panels else 1
        header_limit = max(0, height - footer_rows - chart_budget)
    source = Path(log.path).name
    if ascii_only:
        title = "curve_it"
    mark = ">" if ascii_only else "…"
    # Filenames and timing strings are external input. Keep terminal control bytes out of the frame.
    timing = f"{log.latest_sps:,.0f} steps/s | {log.iter_time}/iter | ETA {log.eta} | Elapsed {log.elapsed}"
    header = [f"{title} | {source}"]
    if log.iters:
        header.append(f"Iteration {log.iters[-1]}/{log.max_iters} | {timing}")
    else:
        header.append("Waiting for the first training iteration." if ascii_only else "等待训练输出")
    if notification_status:
        header.insert(1, notification_status)
    header = [_clip(_visible_text(line, ascii_only=ascii_only), width, mark) for line in header]
    if color:
        header[0] = _paint(header[0], _WHITE, True)
    if hardware is not None:
        compact_hardware = height is not None and height < (25 if live_view else 15)
        rows = render_hardware_panel(hardware, width, ascii_only=ascii_only, color=color, compact=compact_hardware)
        if not live_view and height is not None and len(header) + len(rows) + 1 + footer_rows > height:
            header = header[:1]
        header.extend(rows)
    if not ascii_only and width >= 68 and (height is None or height >= (38 if hardware else 31)) and log.iters:
        notes = (note.lstrip("⏳⚠🧊🏸🚨🏃🌱📈️ ") for note in diagnose_health(log)[:2])
        header.extend(_clip(_visible_text(note), width, mark) for note in notes)
    stop = log.iters[-1] if log.iters else 0
    start = log.iters[0] if log.iters else 0
    if history > 0:
        start = max(start, stop - history + 1)
    if dense and log.iters:
        context = (
            f"History {start}-{stop} | independent Y axes" if ascii_only else f"第 {start}–{stop} 轮 · 各图独立纵轴"
        )
        if height is None or len(header) + 4 <= height:
            header.append(_paint(_clip(context, width, mark), _DIM, color))
    if height is not None:
        if header_limit is None:
            header_limit = max(0, height - footer_rows - int(bool(panels)))
        header = header[:header_limit]
    available = height - len(header) - footer_rows if height is not None else None
    if live_view:
        layout = fit_live(width, available, focused=focused)
    elif dense:
        layout = fit_dense(
            len(panels), width, available if available is not None else max(28, 6 * len(panels)), ascii_only=ascii_only
        )
    else:
        layout = fit_terminal(len(panels), width, available)
    page_size = layout.page_size if height is not None else max(1, len(panels))
    pages = max(1, (len(panels) + page_size - 1) // page_size)
    if selected_key is not None:
        position = next((i for i, panel in enumerate(panels) if panel.key == selected_key), None)
        if position is not None:
            page = position // page_size
    page = min(max(0, page), pages - 1)
    first = page * page_size
    visible = panels[first : first + page_size]
    keys = tuple(panel.key for panel in visible)
    selection = selected_key if selected_key in keys else (keys[0] if keys else None)
    charts = []
    for idx, panel in enumerate(visible, first):
        line_color = _PANEL_COLORS[idx % len(_PANEL_COLORS)]
        if dense:
            plot_height = layout.plot_height + int((idx - first) // layout.columns < layout.extra_plot_rows)
            charts.append(
                _dense_chart(
                    log,
                    panel,
                    layout.cell_width,
                    plot_height,
                    (start, stop),
                    smooth,
                    ascii_only=ascii_only,
                    color=color,
                    line_color=line_color,
                )
            )
        elif layout.compact:
            charts.append(
                [
                    _compact_chart(
                        log, panel, layout.cell_width, smooth, ascii_only=ascii_only, color=line_color if color else ""
                    )
                ]
            )
        else:
            points = log.series.get(panel.key, [])
            points = points[bisect_left(points, (start,)) :]
            label = _visible_text(panel.key if ascii_only else panel.title, ascii_only=ascii_only)
            if points:
                charts.append(
                    render_braille_box(
                        points,
                        label,
                        layout.cell_width - 10,
                        layout.plot_height,
                        smooth,
                        color=color,
                        line_color=line_color,
                        panel=panel,
                        ascii_only=ascii_only,
                        window=(start, stop),
                    )
                )
            else:
                charts.append([_clip(f" {label}  --", layout.cell_width, mark)])
        if live_view and not focused and panel.key == selection:
            row = charts[-1][0]
            prefix = "> " if ascii_only else "› "
            if _vis_width(row) + 2 > layout.cell_width:
                available_width = layout.cell_width - 2
                row = _clip(_ANSI_RE.sub("", row), available_width, mark)
            charts[-1][0] = _paint(prefix + row.lstrip(), _WHITE, color)

    lines = list(header)
    for idx in range(0, len(charts), layout.columns):
        row = charts[idx : idx + layout.columns]
        if idx and not dense and not layout.compact:
            lines.append("")
        for pos in range(max(map(len, row))):
            cells = [chart[pos] if pos < len(chart) else "" for chart in row]
            lines.append(
                (" " * layout.gap)
                .join(cell + " " * max(0, layout.cell_width - _vis_width(cell)) for cell in cells)
                .rstrip()
            )
    if not dense and height is None and not ascii_only and width >= 78 and log.iters and panels:
        lines.extend(_milestones(log, panels, width, color))
    if len(lines) == len(header) and log.iters and not panels:
        lines.append(_clip("Waiting for metrics." if ascii_only else "等待指标", width, mark))
    if watching and live_view:
        if ascii_only:
            mode = "Single" if focused else "Grid"
            controls = "L/R chart  Esc grid" if focused else "L/R page  U/D select  Enter zoom"
        else:
            mode = "单图" if focused else "四图"
            controls = "←→ 切图  Esc 返回" if focused else "←→ 翻页  ↑↓ 选图  Enter 放大"
        state = "Space resume" if paused else "Space pause"
        footer = _clip(f"{mode} {page + 1}/{pages} | {controls} | {state}  r refresh  q quit", width, mark)
        lines = lines[: max(0, height - 1)]
        lines.extend([""] * (height - 1 - len(lines)))
        lines.append(_paint(footer, _WHITE, color))
    elif watching:
        state = "Paused" if paused else "Live"
        paging = f"{page + 1}/{pages} j/k | " if pages > 1 else ""
        coverage = ""
        if charts and width >= 84:
            coverage = (
                (
                    f"{first + 1}–{first + len(charts)} of {len(panels)} charts | "
                    if not ascii_only
                    else f"{first + 1}-{first + len(charts)} of {len(panels)} charts | "
                )
                if pages > 1
                else (f"{len(charts)}/{len(panels)} charts | ")
            )
        controls = "Space pause | r refresh | q quit"
        footer = _clip(f"{state} | {paging}{coverage}{controls}", width, mark)
        lines = lines[: max(0, height - 1)] if height is not None else lines
        lines.append(_paint(footer, _DIM, color))
    return DashboardFrame(tuple(lines), page, pages, keys, selection)


def _milestones(log: ParsedLog, panels: list[Panel], width: int, color: bool) -> list[str]:
    count = min((width - 26) // 13, 6, len(log.iters))
    iters = [log.iters[round(i * (len(log.iters) - 1) / max(1, count - 1))] for i in range(count)]
    lines = ["", _paint("指标" + " " * 22 + "".join(f"{it:>13}" for it in iters), _DIM, color)]
    for panel in panels:
        title = _clip(_visible_text(panel.title), 24)
        points = dict(log.series.get(panel.key, ()))
        values = [panel.format(points[it]) if it in points else "--" for it in iters]
        lines.append(title + " " * (26 - _vis_width(title)) + "".join(f"{value:>13}" for value in values))
    return lines
