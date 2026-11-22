"""Write standalone HTML and optional PNG charts."""

from __future__ import annotations

import math
import os
from contextlib import contextmanager
from html import escape
from pathlib import Path
from uuid import uuid4

from .annotations import PointLabel, ReferenceBand, annotation_values, visible_annotations
from .dashboard import Panel, PanelSpecs, panel_specs
from .hardware import HardwareMeter, HardwareSnapshot, hardware_meters
from .log import ParsedLog
from .metrics import diagnose_health, ema_smooth, format_value
from .profiles import detect_profile


@contextmanager
def _atomic_output(out_path: str):
    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        yield temporary
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def save_png(
    log: ParsedLog,
    panels: PanelSpecs,
    out_path: str,
    smooth: float = 0.6,
    *,
    title: str | None = None,
    hardware: HardwareSnapshot | None = None,
) -> str:
    """Export a high-DPI dark-mode multi-panel PNG chart via matplotlib."""
    try:
        import matplotlib
    except ImportError as error:
        raise ImportError("PNG export requires the png extra. Install with pip install .[png].") from error

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    specs = panel_specs(panels)
    n = len(specs)
    if n == 0:
        raise ValueError("PNG export requires at least one panel")
    cols = 2 if n > 1 else 1
    rows = math.ceil(n / cols)
    plt.style.use("dark_background")
    plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "Noto Sans CJK JP", "AR PL KaitiM GB", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    meters = hardware_meters(hardware) if hardware is not None else []
    meter_columns = 2 if cols == 1 else 3
    meter_rows = math.ceil(len(meters) / meter_columns)
    header_inches = 0.47 + (0.35 + 0.43 * meter_rows if meters else 0.0)
    figure_height = 3.5 * rows + header_inches
    fig, axes = plt.subplots(rows, cols, figsize=(7.2 * cols, figure_height), dpi=140, squeeze=False)
    fig.patch.set_facecolor("#11161d")

    colors = ("#4fc3f7", "#81c784", "#ffb74d", "#e57373", "#ba68c8", "#4dd0e1")
    for idx, panel in enumerate(specs):
        ax = axes[idx // cols][idx % cols]
        ax.set_facecolor("#161c26")
        pts = log.series.get(panel.key, [])
        if pts:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            c = colors[idx % len(colors)]
            ax.plot(xs, ys, color=c, alpha=0.30, linewidth=1.0, label="原始值")
            if smooth > 0.0 and len(ys) > 2:
                ax.plot(xs, ema_smooth(ys, smooth), color=c, linewidth=2.0, label="平滑曲线")
            else:
                ax.plot(xs, ys, color=c, linewidth=2.0)
            annotations = visible_annotations(panel.annotations, xs[0], xs[-1])
            for item in annotations:
                if isinstance(item, PointLabel):
                    ax.plot(item.iteration, item.value, marker="D", markersize=4, color="#f2ca7b", zorder=5)
                    offset = 6 if item.iteration < (xs[0] + xs[-1]) / 2 else -6
                    ax.annotate(
                        item.label,
                        (item.iteration, item.value),
                        xytext=(offset, 5),
                        textcoords="offset points",
                        ha="left" if offset > 0 else "right",
                        color="#f2ca7b",
                        fontsize=8,
                    )
                else:
                    if isinstance(item, ReferenceBand):
                        ax.axhspan(item.lower, item.upper, facecolor="#f2ca7b", alpha=0.13, zorder=0)
                        for edge in (item.lower, item.upper):
                            ax.axhline(edge, color="#a78e5d", linestyle="--", linewidth=0.8)
                        value = item.upper
                    else:
                        value = item.value
                        ax.axhline(value, color="#a78e5d", linestyle="--", linewidth=0.8)
                    ax.annotate(
                        item.label,
                        (0.98, value),
                        xycoords=ax.get_yaxis_transform(),
                        textcoords="offset points",
                        xytext=(0, 4),
                        ha="right",
                        color="#f2ca7b",
                        fontsize=8,
                    )
            ax.margins(y=0.12 if annotations else 0.05)
            ax.set_title(f"{panel.title}（{panel.format(ys[-1])}）", fontsize=11, color="#e6edf3", pad=7)
        else:
            ax.set_title(f"{panel.title}（暂无数据）", fontsize=11, color="#8b949e")
        if panel.unit == "%":
            ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(xmax=1))
        ax.set_xlabel("训练轮数", fontsize=8.5, color="#9da7b3")
        ax.grid(True, color="#2b3442", linestyle="--", linewidth=0.6)
        ax.tick_params(colors="#9da7b3", labelsize=8)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        for sp in ("bottom", "left"):
            ax.spines[sp].set_color("#3b4554")

    for idx in range(n, rows * cols):
        axes[idx // cols][idx % cols].set_visible(False)

    cur_it = log.iters[-1] if log.iters else 0
    pct = (100.0 * cur_it / log.max_iters) if log.max_iters > 0 else 0.0
    stage_label = title or detect_profile(log).title
    fig.suptitle(
        f"{stage_label}  |  进度：第 {cur_it}/{log.max_iters} 轮 ({pct:.1f}%)  |  "
        f"吞吐：{log.latest_sps:,.0f} 步/秒  |  已用时：{log.elapsed}",
        fontsize=12,
        color="#f0f6fc",
        y=1 - 0.10 / figure_height,
    )
    fig.tight_layout(rect=(0, 0, 1, 1 - header_inches / figure_height))
    if meters:
        _png_hardware(fig, meters, meter_columns, meter_rows, figure_height)
    try:
        with _atomic_output(out_path) as temporary:
            fig.savefig(temporary, format="png", facecolor=fig.get_facecolor())
    finally:
        plt.close(fig)
    return out_path


def _png_hardware(fig, meters: list[HardwareMeter], columns: int, rows: int, figure_height: float) -> None:
    height = 0.28 + 0.43 * rows
    ax = fig.add_axes((0.03, 1 - (0.40 + height) / figure_height, 0.94, height / figure_height))
    ax.set_facecolor("#161c26")
    ax.set(xlim=(0, 1), ylim=(0, height), xticks=[], yticks=[])
    for spine in ax.spines.values():
        spine.set_color("#354553")
    gap = 0.025
    cell_width = (1 - (columns + 1) * gap) / columns
    ax.text(gap, height - 0.095, "硬件用量", color="#e6edf3", fontsize=10, va="center", fontweight="bold")
    for index, meter in enumerate(meters):
        row, column = divmod(index, columns)
        left = gap + column * (cell_width + gap)
        y = height - 0.35 - 0.43 * row
        fraction = meter.fraction
        ax.text(left, y, meter.label, color="#edf3fb", fontsize=9.5, va="center")
        if meter.detail:
            ax.text(left + cell_width, y, meter.detail, color="#a4b4c6", fontsize=8, va="center", ha="right")
        ax.barh(y - 0.155, cell_width, left=left, height=0.086, color="#2a3544", linewidth=0)
        if fraction is None:
            ax.barh(
                y - 0.155,
                cell_width,
                left=left,
                height=0.086,
                facecolor="none",
                edgecolor="#637184",
                hatch="////",
                linewidth=0,
            )
        elif fraction:
            ax.barh(y - 0.155, cell_width * fraction, left=left, height=0.086, color=meter.color, linewidth=0)


def _html_meter(meter: HardwareMeter) -> str:
    label = escape(meter.label)
    fraction = meter.fraction
    if fraction is None:
        track = f'<div class="meter-track missing" aria-label="{label} 暂无读数"></div>'
    else:
        track = (
            f'<div class="meter-track" role="meter" aria-label="{label}" '
            f'aria-valuemin="0" aria-valuemax="100" aria-valuenow="{fraction * 100:.5g}">'
            f'<span style="inline-size:{fraction * 100:.6g}%;background:{meter.color}"></span></div>'
        )
    return (
        f'<div class="meter"><div class="meter-label"><b>{label}</b>'
        f"<span>{escape(meter.detail)}</span></div>{track}</div>"
    )


def _svg_panel(pts: list[tuple[int, float]], panel: Panel, color: str, smooth: float = 0.6) -> str:
    title = escape(panel.title)
    w, h = 540, 210
    pad_l, pad_r, pad_t, pad_b = 58, 18, 28, 28
    if not pts:
        return f'<div class="card"><h3>{title}</h3><p class="empty">暂无数据</p></div>'
    xs = [p[0] for p in pts]
    ys_raw = [p[1] for p in pts]
    ys_s = ema_smooth(ys_raw, smooth) if smooth > 0.0 else ys_raw
    x_min, x_max = min(xs), max(xs)
    annotations = visible_annotations(panel.annotations, x_min, x_max)
    bounds = ys_raw + ys_s + annotation_values(annotations)
    y_min, y_max = min(bounds), max(bounds)
    if abs(y_max - y_min) < 1e-9:
        y_min -= 0.1
        y_max += 0.1
    elif annotations:
        pad = (y_max - y_min) * 0.10
        y_min, y_max = y_min - pad, y_max + pad

    def sx(x: float) -> float:
        return pad_l + (x - x_min) / max(1, x_max - x_min) * (w - pad_l - pad_r)

    def sy(y: float) -> float:
        return pad_t + (1.0 - (y - y_min) / (y_max - y_min)) * (h - pad_t - pad_b)

    raw_poly = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in zip(xs, ys_raw))
    sm_poly = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in zip(xs, ys_s))
    grid_lines = []
    for frac in (0.0, 0.5, 1.0):
        yv = y_min + frac * (y_max - y_min)
        yp = sy(yv)
        grid_lines.append(
            f'<line x1="{pad_l}" y1="{yp:.1f}" x2="{w - pad_r}" y2="{yp:.1f}" stroke="#2b3442" stroke-dasharray="3,3"/>'
            f'<text x="{pad_l - 6}" y="{yp + 4:.1f}" text-anchor="end" fill="#9da7b3" font-size="11">{f"{yv:.1%}" if panel.unit == "%" else format_value(yv)}</text>'
        )
    guides = []
    labels = []
    for item in annotations:
        x, anchor = w - pad_r - 5, "end"
        if isinstance(item, ReferenceBand):
            upper, lower = sy(item.upper), sy(item.lower)
            y = upper
            guides.append(
                f'<rect class="reference-band" x="{pad_l}" y="{upper:.2f}" '
                f'width="{w - pad_l - pad_r}" height="{lower - upper:.2f}"/>'
            )
            for edge in (upper, lower):
                guides.append(
                    f'<line class="reference-line" x1="{pad_l}" x2="{w - pad_r}" y1="{edge:.2f}" y2="{edge:.2f}"/>'
                )
        elif isinstance(item, PointLabel):
            px, y = sx(item.iteration), sy(item.value)
            if px < w / 2:
                x, anchor = px + 7, "start"
            else:
                x = px - 7
            labels.append(f'<circle class="point-label" cx="{px:.2f}" cy="{y:.2f}" r="3.5"/>')
        else:
            y = sy(item.value)
            guides.append(f'<line class="reference-line" x1="{pad_l}" x2="{w - pad_r}" y1="{y:.2f}" y2="{y:.2f}"/>')
        labels.append(
            f'<text class="annotation-label" x="{x:.2f}" y="{y - 5:.2f}" text-anchor="{anchor}">'
            f"{escape(item.label)}</text>"
        )
    return (
        f'<div class="card">'
        f'<h3>{title} <span class="val">{escape(panel.format(ys_raw[-1]))}</span></h3>'
        f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{title}">'
        + "".join(grid_lines + guides)
        + f'<polyline fill="none" stroke="{color}" stroke-opacity="0.28" stroke-width="1.3" points="{raw_poly}"/>'
        + f'<polyline fill="none" stroke="{color}" stroke-width="2.2" points="{sm_poly}"/>'
        + "".join(labels)
        + f'<text x="{pad_l}" y="{h - 6}" fill="#9da7b3" font-size="11">第 {x_min} 轮</text>'
        + f'<text x="{w - pad_r}" y="{h - 6}" text-anchor="end" fill="#9da7b3" font-size="11">第 {x_max} 轮</text>'
        + "</svg></div>"
    )


def save_html(
    log: ParsedLog,
    panels: PanelSpecs,
    out_path: str,
    smooth: float = 0.6,
    refresh_s: int = 0,
    *,
    title: str | None = None,
    hardware: HardwareSnapshot | None = None,
) -> str:
    """Export a zero-dependency standalone HTML dashboard with crisp inline SVG curves."""
    colors = ("#4fc3f7", "#81c784", "#ffb74d", "#e57373", "#ba68c8", "#4dd0e1")
    cards = "\n".join(
        _svg_panel(log.series.get(panel.key, []), panel, colors[i % len(colors)], smooth)
        for i, panel in enumerate(panel_specs(panels))
    )
    notes = "".join(f"<li>{escape(note.lstrip('⏳⚠🧊🏸🚨🏃🌱📈️ '))}</li>" for note in diagnose_health(log))
    cur_it = log.iters[-1] if log.iters else 0
    stage_label = escape(title or detect_profile(log).title)
    refresh_meta = f'<meta http-equiv="refresh" content="{refresh_s}">' if refresh_s > 0 else ""
    hardware_panel = ""
    if hardware is not None:
        readouts = "".join(_html_meter(meter) for meter in hardware_meters(hardware))
        hardware_panel = (
            f'<section class="hardware" aria-label="硬件用量"><h3>硬件用量</h3>'
            f'<div class="meters">{readouts}</div></section>'
        )
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">{refresh_meta}
<title>{stage_label} - {escape(os.path.basename(log.path))}</title>
<style>
  body {{ background:#0d1117; color:#e6edf3; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; margin:18px; }}
  .head {{ border-bottom:1px solid #30363d; padding:8px 0 14px; margin-bottom:16px; }}
  .head h2 {{ margin:0 0 8px 0; font-size:17px; }}
  .meta {{ color:#8b949e; font-size:13px; margin-bottom:8px; }}
  .notes {{ margin:6px 0 0 18px; padding:0; font-size:13px; line-height:1.6; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%, 460px),1fr)); gap:10px 18px; }}
  .card {{ background:#161b22; border-top:1px solid #30363d; padding:12px 14px; min-width:0; }}
  .card h3 {{ margin:0 0 6px 0; font-size:14px; display:flex; justify-content:space-between; gap:8px; flex-wrap:wrap; }}
  .val {{ color:#58a6ff; font-family:monospace; }}
  .empty {{ color:#8b949e; font-size:13px; }}
  .hardware {{ background:#161c26; border-top:1px solid #354553; margin:0 0 16px; padding:12px 14px; }}
  .hardware h3 {{ margin:0 0 10px; font-size:13px; }}
  .meters {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,17rem),1fr)); gap:12px 18px; }}
  .meter-label {{ display:flex; justify-content:space-between; align-items:baseline; flex-wrap:wrap; gap:3px 8px; margin-bottom:4px; font-size:12px; }}
  .meter-label span {{ color:#a4b4c6; font-variant-numeric:tabular-nums; }}
  .meter-track {{ block-size:9px; overflow:hidden; background:#2a3544; }}
  .meter-track span {{ display:block; block-size:100%; }}
  .reference-band {{ fill:#f2ca7b; fill-opacity:0.13; }}
  .reference-line {{ stroke:#a78e5d; stroke-dasharray:4 3; }}
  .point-label {{ fill:#f2ca7b; }}
  .annotation-label {{ fill:#f2ca7b; font-size:11px; stroke:#161c26; stroke-width:3px; paint-order:stroke; stroke-linejoin:round; }}
  .meter-track.missing {{ background:repeating-linear-gradient(135deg,#2a3544 0 3px,#48576a 3px 4px); }}
</style>
</head>
<body>
  <div class="head">
    <h2>{stage_label}（{escape(os.path.basename(log.path))}）</h2>
    <div class="meta">第 {cur_it}/{log.max_iters} 轮 &nbsp;|&nbsp; 吞吐 {log.latest_sps:,.0f} 步/秒 &nbsp;|&nbsp; 单轮 {escape(log.iter_time)} &nbsp;|&nbsp; 已用时 {escape(log.elapsed)} &nbsp;|&nbsp; 预计剩余 {escape(log.eta)}</div>
    <ul class="notes">{notes}</ul>
  </div>
  {hardware_panel}
  <main class="grid">{cards}</main>
</body>
</html>
"""
    with _atomic_output(out_path) as temporary:
        temporary.write_text(html, encoding="utf-8")
    return out_path
