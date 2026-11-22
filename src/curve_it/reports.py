"""Build training notifications and schedule them from live log refreshes."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from .dashboard import Panel
from .log import ParsedLog
from .notifications import Notification, NotificationError, Notifier


def parse_interval(value: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]{0,8})([smh])", value)
    if match is None:
        raise ValueError("--notify-every expects a positive duration such as 10m or 30m")
    count, unit = match.groups()
    return int(count) * {"s": 1, "m": 60, "h": 3600}[unit]


def validate_chart_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        if (
            parts.scheme == "https"
            and parts.hostname
            and parts.username is None
            and not re.search(r"[\s\x00-\x1f<>\x7f]", value)
        ):
            return value
    except ValueError:
        pass
    raise ValueError("CURVE_IT_CHART_URL must be a valid HTTPS image URL")


def _inline(text: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()<>#!|])", r"\\\1", " ".join(text.split()))


def training_report(
    log: ParsedLog, panels: Sequence[Panel], stage: str, *, chart_url: str = ""
) -> Notification | None:
    """Build a Markdown summary from the most recent iteration with selected metrics."""
    samples = [(panel, log.series[panel.key][-1]) for panel in panels if log.series.get(panel.key)]
    if not samples:
        return None
    iteration = max(point[0] for _, point in samples)
    progress = f"Iteration {iteration}/{log.max_iters}"
    if log.latest_sps and log.iters and log.iters[-1] == iteration:
        progress += f" · {log.latest_sps:,.0f} steps/s · ETA {_inline(log.eta)}"
    body = [f"**运行** {_inline(Path(log.path).name)}", "", progress, ""]
    for panel, (step, value) in samples:
        when = f"（第 {step} 轮）" if step != iteration else ""
        body.append(f"- {_inline(panel.title)} **{panel.format(value)}**{when}")
    if chart_url:
        body.extend(("", f"![训练曲线](<{validate_chart_url(chart_url)}>)"))
    return Notification(f"{stage} · 第 {iteration} 轮", "\n".join(body), f"Iteration {iteration}/{log.max_iters}")


class ReportSchedule:
    """Send at most one report per interval without making refresh wait on the network."""

    def __init__(self, notifier: Notifier, interval: float, *, chart_url: str = "") -> None:
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("report interval must be finite and positive")
        self._notifier = notifier
        self._interval = interval
        self._chart_url = validate_chart_url(chart_url) if chart_url else ""
        self._due = time.monotonic() + interval
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="curve-notify")
        self._pending: Future[None] | None = None
        self._attempt: tuple[ParsedLog, int] | None = None
        self._delivered: tuple[ParsedLog, int] | None = None
        self.error = ""

    def update(self, log: ParsedLog, panels: Sequence[Panel], stage: str) -> None:
        if self._pending is not None:
            if not self._pending.done():
                return
            self._collect()
        now = time.monotonic()
        if now < self._due:
            return
        self._due = now + self._interval
        iteration = max((log.series[p.key][-1][0] for p in panels if log.series.get(p.key)), default=-1)
        if iteration == -1:
            return
        if self._delivered is not None and self._delivered[0] is log and self._delivered[1] == iteration:
            return
        message = training_report(log, panels, stage, chart_url=self._chart_url)
        if message is not None:
            self._attempt = (log, iteration)
            self._pending = self._executor.submit(self._notifier.send, message)

    def _collect(self) -> None:
        if self._pending is None:
            return
        try:
            self._pending.result()
        except NotificationError as error:
            self.error = str(error)
        else:
            self._delivered = self._attempt
            self.error = ""
        self._pending = None
        self._attempt = None

    def close(self) -> None:
        self._executor.shutdown()
        self._collect()
