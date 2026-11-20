"""Write standalone HTML and optional PNG charts."""

from __future__ import annotations

import math
import os
from html import escape

from .log import ParsedLog
from .metrics import diagnose_health, ema_smooth, format_value


def save_png(log: ParsedLog, panels: list[tuple[str, str]], out_path: str, smooth: float = 0.6) -> str:
    """Export a high-DPI dark-mode multi-panel PNG chart via matplotlib."""
    try:
        import matplotlib
    except ImportError as error:
        raise ImportError("PNG export requires the png extra. Install with pip install .[png].") from error

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n = len(panels)
    cols = 2 if n > 1 else 1
    rows = math.ceil(n / cols)
    plt.style.use("dark_background")
    plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "Noto Sans CJK JP", "AR PL KaitiM GB", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(rows, cols, figsize=(7.2 * cols, 3.5 * rows), dpi=140, squeeze=False)
    fig.patch.set_facecolor("#11161d")

    colors = ("#4fc3f7", "#81c784", "#ffb74d", "#e57373", "#ba68c8", "#4dd0e1")
    for idx, (key, title) in enumerate(panels):
        ax = axes[idx // cols][idx % cols]
        ax.set_facecolor("#161c26")
        pts = log.series.get(key, [])
        if pts:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            c = colors[idx % len(colors)]
            ax.plot(xs, ys, color=c, alpha=0.30, linewidth=1.0, label="原始值")
            if smooth > 0.0 and len(ys) > 2:
                ax.plot(xs, ema_smooth(ys, smooth), color=c, linewidth=2.0, label="平滑曲线")
            else:
                ax.plot(xs, ys, color=c, linewidth=2.0)
            ax.set_title(f"{title}（最新：{format_value(ys[-1])}）", fontsize=11, color="#e6edf3", pad=7)
        else:
            ax.set_title(f"{title}（暂无数据）", fontsize=11, color="#8b949e")
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
    stage_label = "阶段二 击球与发球训练曲线" if log.is_stage2() else "阶段一 敏捷移动训练曲线"
    fig.suptitle(
        f"{stage_label}  |  进度：第 {cur_it}/{log.max_iters} 轮 ({pct:.1f}%)  |  "
        f"吞吐：{log.latest_sps:,.0f} 步/秒  |  已用时：{log.elapsed}",
        fontsize=12,
        color="#f0f6fc",
        y=0.992,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    fig.savefig(out_path, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out_path


def _svg_panel(pts: list[tuple[int, float]], title: str, color: str, smooth: float = 0.6) -> str:
    title = escape(title)
    w, h = 540, 210
    pad_l, pad_r, pad_t, pad_b = 58, 18, 28, 28
    if not pts:
        return f'<div class="card"><h3>{title}</h3><p class="empty">暂无数据</p></div>'
    xs = [p[0] for p in pts]
    ys_raw = [p[1] for p in pts]
    ys_s = ema_smooth(ys_raw, smooth) if smooth > 0.0 else ys_raw
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(min(ys_raw), min(ys_s)), max(max(ys_raw), max(ys_s))
    if abs(y_max - y_min) < 1e-9:
        y_min -= 0.1
        y_max += 0.1
    if x_max == x_min:
        x_max = x_min + 1

    def sx(x: float) -> float:
        return pad_l + (x - x_min) / (x_max - x_min) * (w - pad_l - pad_r)

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
            f'<text x="{pad_l - 6}" y="{yp + 4:.1f}" text-anchor="end" fill="#9da7b3" font-size="11">{format_value(yv)}</text>'
        )
    return (
        f'<div class="card">'
        f'<h3>{title} <span class="val">最新 {format_value(ys_raw[-1])}</span></h3>'
        f'<svg viewBox="0 0 {w} {h}" width="100%">'
        + "".join(grid_lines)
        + f'<polyline fill="none" stroke="{color}" stroke-opacity="0.28" stroke-width="1.3" points="{raw_poly}"/>'
        + f'<polyline fill="none" stroke="{color}" stroke-width="2.2" points="{sm_poly}"/>'
        + f'<text x="{pad_l}" y="{h - 6}" fill="#9da7b3" font-size="11">第 {x_min} 轮</text>'
        + f'<text x="{w - pad_r}" y="{h - 6}" text-anchor="end" fill="#9da7b3" font-size="11">第 {x_max} 轮</text>'
        + "</svg></div>"
    )


def save_html(
    log: ParsedLog, panels: list[tuple[str, str]], out_path: str, smooth: float = 0.6, refresh_s: int = 0
) -> str:
    """Export a zero-dependency standalone HTML dashboard with crisp inline SVG curves."""
    colors = ("#4fc3f7", "#81c784", "#ffb74d", "#e57373", "#ba68c8", "#4dd0e1")
    cards = "\n".join(
        _svg_panel(log.series.get(k, []), title, colors[i % len(colors)], smooth) for i, (k, title) in enumerate(panels)
    )
    notes = "".join(f"<li>{escape(note.lstrip('⏳⚠🧊🏸🚨🏃🌱📈️ '))}</li>" for note in diagnose_health(log))
    cur_it = log.iters[-1] if log.iters else 0
    stage_label = "阶段二 击球与发球训练曲线" if log.is_stage2() else "阶段一 敏捷移动训练曲线"
    refresh_meta = f'<meta http-equiv="refresh" content="{refresh_s}">' if refresh_s > 0 else ""
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">{refresh_meta}
<title>{stage_label} - {escape(os.path.basename(log.path))}</title>
<style>
  body {{ background:#0d1117; color:#e6edf3; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; margin:18px; }}
  .head {{ background:#161b22; border:1px solid #30363d; border-radius:10px; padding:14px 18px; margin-bottom:16px; }}
  .head h2 {{ margin:0 0 8px 0; font-size:17px; }}
  .meta {{ color:#8b949e; font-size:13px; margin-bottom:8px; }}
  .notes {{ margin:6px 0 0 18px; padding:0; font-size:13px; line-height:1.6; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%, 460px),1fr)); gap:14px; }}
  .card {{ background:#161b22; border:1px solid #30363d; border-radius:10px; padding:12px 14px; }}
  .card h3 {{ margin:0 0 6px 0; font-size:14px; display:flex; justify-content:space-between; }}
  .val {{ color:#58a6ff; font-family:monospace; }}
  .empty {{ color:#8b949e; font-size:13px; }}
</style>
</head>
<body>
  <div class="head">
    <h2>{stage_label}（{escape(os.path.basename(log.path))}）</h2>
    <div class="meta">第 {cur_it}/{log.max_iters} 轮 &nbsp;|&nbsp; 吞吐 {log.latest_sps:,.0f} 步/秒 &nbsp;|&nbsp; 单轮 {escape(log.iter_time)} &nbsp;|&nbsp; 已用时 {escape(log.elapsed)} &nbsp;|&nbsp; 预计剩余 {escape(log.eta)}</div>
    <ul class="notes">{notes}</ul>
  </div>
  <div class="grid">{cards}</div>
</body>
</html>
"""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path
