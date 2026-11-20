"""Read numeric training metrics from RSL-RL console logs."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

_ITER_RE = re.compile(r"Learning iteration\s+(\d+)/(\d+)")
_COMP_RE = re.compile(r"Computation:\s+([\d.]+)\s+steps/s")
_KV_RE = re.compile(r"^\s*([A-Za-z0-9_./ -]+?):\s+([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*$")
_STR_KV_RE = re.compile(r"^\s*(Time elapsed|ETA|Iteration time):\s+(.+?)\s*$")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@dataclass
class ParsedLog:
    path: str
    iters: list[int] = field(default_factory=list)
    max_iters: int = 0
    series: dict[str, list[tuple[int, float]]] = field(default_factory=dict)
    elapsed: str = "-"
    eta: str = "-"
    iter_time: str = "-"
    latest_sps: float = 0.0

    def latest(self, key: str) -> float | None:
        pts = self.series.get(key)
        return pts[-1][1] if pts else None

    def is_stage2(self) -> bool:
        return any(k.startswith("Hit/") or k.startswith("HitCur/") for k in self.series)


def parse_console_log(path: str | Path) -> ParsedLog:
    """Parse an RSL-RL console log file into per-metric ``(iter, value)`` series."""
    out = ParsedLog(path=str(path))
    cur_iter: int | None = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            if not raw.endswith("\n"):
                break  # A live writer can leave a partial final line.
            line = _ANSI_RE.sub("", raw.rstrip("\r\n"))
            m = _ITER_RE.search(line)
            if m:
                iteration = int(m.group(1))
                if cur_iter is not None and iteration < cur_iter:
                    out = ParsedLog(path=str(path))
                if not out.iters or out.iters[-1] != iteration:
                    out.iters.append(iteration)
                cur_iter = iteration
                out.max_iters = int(m.group(2))
                continue
            if cur_iter is None:
                continue
            mc = _COMP_RE.search(line)
            if mc:
                sps = float(mc.group(1))
                out.latest_sps = sps
                out.series.setdefault("Steps/s", []).append((cur_iter, sps))
                continue
            ms = _STR_KV_RE.match(line)
            if ms:
                k, v = ms.group(1), ms.group(2)
                if k == "Time elapsed":
                    out.elapsed = v
                elif k == "ETA":
                    out.eta = v
                elif k == "Iteration time":
                    out.iter_time = v
                    try:
                        out.series.setdefault("Iteration time (s)", []).append((cur_iter, float(v.rstrip("s"))))
                    except ValueError:
                        pass
                continue
            mk = _KV_RE.match(line)
            if mk:
                key = mk.group(1).strip()
                val = float(mk.group(2))
                if math.isfinite(val):
                    out.series.setdefault(key, []).append((cur_iter, val))
    return out


def find_latest_log(directory: Path, pattern: str) -> Path:
    """Select the newest matching log, breaking timestamp ties by name."""
    files = (file for file in directory.glob(pattern) if file.is_file())
    newest = max(files, key=lambda file: (file.stat().st_mtime_ns, file.name), default=None)
    if newest is None:
        raise FileNotFoundError(f"no logs match {directory / pattern}")
    return newest.resolve()
