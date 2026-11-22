"""Read numeric training metrics from RSL-RL console logs."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

_ITER_RE = re.compile(r"Learning iteration\s+(\d+)\s*/\s*(\d+)")
_COMP_RE = re.compile(r"Computation:\s+([\d.]+)\s+steps/s")
_KV_RE = re.compile(r"^\s*([A-Za-z0-9_./ -]+?):\s+([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*$")
_STR_KV_RE = re.compile(r"^\s*(Time elapsed|ETA|Iteration time):\s+(.+?)\s*$")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_TIMINGS = {"Time elapsed": "elapsed", "ETA": "eta", "Iteration time": "iter_time"}
_ANCHOR_SIZE = 512


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
        return any(k.startswith(("Hit/", "HitCur/")) for k in self.series)


class LogReader:
    """Follow one log by reading appended lines. Replacements start a fresh snapshot."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.log = ParsedLog(str(path))
        self.revision = 0
        self._offset = 0
        self._anchor: bytes = b""
        self._stamp: tuple[int, ...] | None = None

    def read(self) -> ParsedLog:
        """Refresh the current snapshot and advance ``revision`` when its contents change."""
        with self.path.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            stamp = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            if self._stamp == stamp:
                return self.log
            replaced = self._stamp is not None and stamp[:2] != self._stamp[:2]
            if replaced or stat.st_size < self._offset or not self._matches_anchor(stream):
                self.log = ParsedLog(str(self.path))
                self._offset = 0
                self.revision += 1
            stream.seek(self._offset)
            changed = False
            # Bound the read to the observed size so a fast writer cannot starve redraws.
            remaining = stat.st_size - self._offset
            while remaining > 0:
                raw = stream.readline(remaining)
                if not raw.endswith(b"\n"):
                    break
                count = len(raw)
                self._offset += count
                remaining -= count
                line = _ANSI_RE.sub("", raw.decode("utf-8", errors="replace").rstrip("\r\n"))
                changed = self._parse_line(line) or changed
            stream.seek(max(0, self._offset - _ANCHOR_SIZE))
            self._anchor = stream.read(min(self._offset, _ANCHOR_SIZE))
            self._stamp = stamp
            self.revision += int(changed)
        return self.log

    def _matches_anchor(self, stream: BinaryIO) -> bool:
        stream.seek(self._offset - len(self._anchor))
        return stream.read(len(self._anchor)) == self._anchor

    def _parse_line(self, line: str) -> bool:
        if match := _ITER_RE.search(line):
            iteration, limit = map(int, match.groups())
            if self.log.iters and iteration < self.log.iters[-1]:
                self.log = ParsedLog(str(self.path))
            changed = self.log.max_iters != limit or not self.log.iters or self.log.iters[-1] != iteration
            if not self.log.iters or self.log.iters[-1] != iteration:
                self.log.iters.append(iteration)
            self.log.max_iters = limit
            return changed
        if not self.log.iters:
            return False
        if match := _COMP_RE.search(line):
            self.log.latest_sps = float(match[1])
            return self._store("Steps/s", self.log.latest_sps)
        if match := _STR_KV_RE.match(line):
            attr, text = _TIMINGS[match[1]], match[2]
            changed = getattr(self.log, attr) != text
            setattr(self.log, attr, text)
            if attr == "iter_time":
                try:
                    changed = self._store("Iteration time (s)", float(text.removesuffix("s"))) or changed
                except ValueError:
                    pass
            return changed
        if match := _KV_RE.match(line):
            return self._store(match[1].strip(), float(match[2]))
        return False

    def _store(self, key: str, value: float) -> bool:
        if not math.isfinite(value):
            return False
        point = (self.log.iters[-1], value)
        points = self.log.series.setdefault(key, [])
        if points and points[-1][0] == point[0]:
            changed = points[-1] != point
            points[-1] = point
            return changed
        points.append(point)
        return True


def parse_console_log(path: str | Path) -> ParsedLog:
    """Parse a standalone snapshot using the same rules as live monitoring."""
    return LogReader(path).read()


def find_latest_log(directory: Path, pattern: str) -> Path:
    """Select the newest matching log, breaking timestamp ties by name."""
    newest: tuple[int, str, Path] | None = None
    for path in directory.glob(pattern):
        try:
            mtime = path.stat().st_mtime_ns
            if path.is_file():
                candidate = (mtime, path.name, path)
                if newest is None or candidate > newest:
                    newest = candidate
        except FileNotFoundError:
            continue
    if newest is None:
        raise FileNotFoundError(f"no logs match {directory / pattern}")
    return newest[2].resolve()
