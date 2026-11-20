"""Terminal charts with Unicode and ASCII rendering."""

from __future__ import annotations

import re
from unicodedata import combining, east_asian_width

from .log import ParsedLog
from .metrics import diagnose_health, ema_smooth, format_value

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
_LOWER_IS_BETTER = ("fall", "fault", "miss", "err", "clip", "stall")


def _paint(text: str, code: str, color: bool) -> str:
    return f"{code}{text}{_RESET}" if color else text


def _vis_width(text: str) -> int:
    clean = _ANSI_RE.sub("", text)
    return sum(2 if east_asian_width(ch) in {"F", "W"} else 1 for ch in clean if not combining(ch))


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
) -> list[str]:
    """Render a compact high-resolution Unicode Braille line plot (optionally ANSI 256-colored)."""
    if not pts:
        return [f" {_paint(title, _WHITE, color)}: {_paint('(暂无数据)', _DIM, color)}"]
    xs = [p[0] for p in pts]
    ys_raw = [p[1] for p in pts]
    ys = ema_smooth(ys_raw, smooth) if smooth > 0.0 else ys_raw

    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    if abs(y_max - y_min) < 1e-9:
        pad = max(abs(y_max) * 0.1, 0.1)
        y_min -= pad
        y_max += pad
    if x_max == x_min:
        x_max = x_min + 1

    pw = width * 2
    ph = height * 4
    grid = [[0] * width for _ in range(height)]

    def plot_px(px: int, py: int) -> None:
        if 0 <= px < pw and 0 <= py < ph:
            cx, subx = divmod(px, 2)
            cy, suby = divmod(py, 4)
            grid[cy][cx] |= _BRAILLE_BITS[suby][subx]

    # Map points and connect consecutive pixels with Bresenham segments
    prev_px: int | None = None
    prev_py: int | None = None
    for x, y in zip(xs, ys):
        px = int(round((x - x_min) / (x_max - x_min) * (pw - 1)))
        py = int(round((1.0 - (y - y_min) / (y_max - y_min)) * (ph - 1)))
        if prev_px is not None and prev_py is not None:
            steps = max(abs(px - prev_px), abs(py - prev_py), 1)
            for s in range(steps + 1):
                ix = int(round(prev_px + (px - prev_px) * s / steps))
                iy = int(round(prev_py + (py - prev_py) * s / steps))
                plot_px(ix, iy)
        else:
            plot_px(px, py)
        prev_px, prev_py = px, py

    cur_v = ys_raw[-1]
    delta = ys_raw[-1] - ys_raw[0]
    sign = "+" if delta >= 0 else ""
    improved = (delta <= 0) if lower_is_better else (delta >= 0)
    delta_col = _GREEN if improved else _RED
    dot = _paint("●", line_color, color)
    header = (
        f" {dot} {_paint(title, _WHITE, color)}  "
        f"{_paint(format_value(cur_v), _BOLD + line_color, color)} "
        f"{_paint(f'({sign}{format_value(delta)})', delta_col, color)}"
    )
    y_hi = _paint(f"{format_value(y_max):>8} ┐", _DIM, color)
    y_mid = _paint(f"{format_value(0.5 * (y_min + y_max)):>8} │", _DIM, color)
    y_bar = _paint(f"{' ' * 8} │", _DIM, color)
    y_lo = _paint(f"{format_value(y_min):>8} └", _DIM, color)
    blank_y = " " * 8

    def row_str(row_cells: list[int]) -> str:
        return _paint("".join(chr(0x2800 + c) for c in row_cells), line_color, color)

    lines = [header]
    lines.append(y_hi + row_str(grid[0]))
    mid_row = height // 2
    for r in range(1, height - 1):
        lines.append((y_mid if r == mid_row else y_bar) + row_str(grid[r]))
    if height > 1:
        lines.append(y_lo + row_str(grid[-1]))
    x_axis = f"{x_min}" + " " * max(1, width - len(str(x_min)) - len(str(x_max))) + f"{x_max}"
    lines.append(_paint(f"{blank_y}  {x_axis}", _DIM, color))
    return lines


def print_terminal_dashboard(
    log: ParsedLog, panels: list[tuple[str, str]], smooth: float = 0.0, *, color: bool = True
) -> None:
    """Print colored summary banner, progress bar, 2-column Braille plots, and milestone table."""
    cur_it = log.iters[-1] if log.iters else 0
    pct = (100.0 * cur_it / log.max_iters) if log.max_iters > 0 else 0.0
    bar_w = 18
    filled = int(round(bar_w * min(max(pct / 100.0, 0.0), 1.0)))
    bar = _paint("█" * filled, _CYAN, color) + _paint("░" * (bar_w - filled), _DIM, color)
    stage_label = "阶段二 · 击球与发球" if log.is_stage2() else "阶段一 · 敏捷移动"

    rule = _paint("━" * 104, _BORDER, color)
    print(rule)
    print(
        f" {_paint('📊 ' + stage_label, _WHITE, color)}  "
        f"[{bar}] {_paint(f'{cur_it}/{log.max_iters} ({pct:.1f}%)', _BOLD + _CYAN, color)}  "
        f"{_paint('|', _DIM, color)}  {_paint(f'{log.latest_sps:,.0f} 步/秒', _GREEN, color)}  "
        f"{_paint('|', _DIM, color)}  单轮 {log.iter_time}  "
        f"{_paint('|', _DIM, color)}  已跑 {log.elapsed}  "
        f"{_paint('|', _DIM, color)}  剩余 {log.eta}"
    )
    for note in diagnose_health(log):
        if note.startswith("🚨"):
            c = _BOLD + _RED
        elif note.startswith("⚠️"):
            c = _BOLD + _YELLOW
        elif note.startswith(("🏃", "🏸")):
            c = _GREEN
        else:
            c = _CYAN
        print(f" {_paint(note, c, color)}")
    print(rule)

    if not log.iters:
        return

    # 2-column Braille plot grid
    box_w, box_h = 38, 7
    col_w = 52
    rendered = [
        render_braille_box(
            log.series.get(k, []),
            title,
            box_w,
            box_h,
            smooth,
            color=color,
            line_color=_PANEL_COLORS[i % len(_PANEL_COLORS)],
            lower_is_better=any(tok in k.lower() for tok in _LOWER_IS_BETTER),
        )
        for i, (k, title) in enumerate(panels)
    ]
    for i in range(0, len(rendered), 2):
        left = rendered[i]
        right = rendered[i + 1] if i + 1 < len(rendered) else [""] * len(left)
        n_rows = max(len(left), len(right))
        for r in range(n_rows):
            l_txt = left[r] if r < len(left) else ""
            r_txt = right[r] if r < len(right) else ""
            pad = max(2, col_w - _vis_width(l_txt))
            print(f"{l_txt}{' ' * pad}{r_txt}")
        print()

    # Compact Chinese milestone table (up to 6 evenly spaced iterations)
    n_it = len(log.iters)
    idxs = sorted({int(round(j * (n_it - 1) / min(5, n_it - 1))) for j in range(min(6, n_it))}) if n_it > 1 else [0]
    pick_iters = [log.iters[i] for i in idxs]
    lookup = {k: dict(log.series.get(k, [])) for k, _ in panels}

    def rpad(text: str, width: int) -> str:
        return " " * max(1, width - _vis_width(text)) + text

    name_w = 22
    col_w_tbl = 11
    hdr_lbl = "指标"
    hdr = _paint(hdr_lbl + " " * (name_w - _vis_width(hdr_lbl)), _BOLD + _CYAN, color)
    for j, it in enumerate(pick_iters):
        col = rpad(f"第{it}轮", col_w_tbl)
        hdr += _paint(col, _BOLD + (_WHITE if j == len(pick_iters) - 1 else _CYAN), color)
    print(hdr)
    print(_paint("─" * (name_w + col_w_tbl * len(pick_iters)), _BORDER, color))
    for i, (k, title) in enumerate(panels):
        c = _PANEL_COLORS[i % len(_PANEL_COLORS)]
        lbl = f"● {title}"
        row = _paint("● ", c, color) + _paint(title, _WHITE, color) + " " * max(1, name_w - _vis_width(lbl))
        for j, it in enumerate(pick_iters):
            v = lookup[k].get(it)
            cell = rpad(format_value(v) if v is not None else "-", col_w_tbl)
            row += _paint(cell, _BOLD + c, color) if j == len(pick_iters) - 1 else cell
        print(row)


def print_ascii_dashboard(log: ParsedLog, panels: list[tuple[str, str]], width: int = 80, smooth: float = 0.0) -> None:
    """Print a compact dashboard using only characters supported by a basic Linux console."""
    source = log.path.rsplit("/", 1)[-1].encode("ascii", errors="backslashreplace").decode("ascii")
    print(f"curve_it | {source}"[:width])
    if not log.iters:
        print("Waiting for the first training iteration.")
        return
    print(f"Iteration {log.iters[-1]}/{log.max_iters} | {log.latest_sps:,.0f} steps/s | ETA {log.eta}"[:width])
    name_width = min(42, max(16, width // 2))
    plot_width = max(0, width - name_width - 13)
    symbols = "._-~=+*#"
    for key, _ in panels:
        points = log.series.get(key, ())
        label = key[:name_width]
        if not points:
            print(f"{label:{name_width}} {'--':>10}"[:width])
            continue
        values = ema_smooth([value for _, value in points], smooth)
        lo, hi = min(values), max(values)
        count = min(plot_width, len(values))
        samples = [values[round(i * (len(values) - 1) / max(1, count - 1))] for i in range(count)]
        chart = "".join(symbols[round((v - lo) / (hi - lo) * 7)] if hi > lo else "-" for v in samples)
        print(f"{label:{name_width}} {format_value(points[-1][1]):>10}  {chart}"[:width])
