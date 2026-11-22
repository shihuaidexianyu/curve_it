"""Read system load and NVIDIA device counters for the dashboard."""

from __future__ import annotations

import csv
import math
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ._gpm import read_sm_counters

_PROC = Path("/proc")
_CGROUP = Path("/sys/fs/cgroup")
_GIB = 1024**3


@dataclass(frozen=True)
class CpuUsage:
    physical_cores: int | None
    logical_cpus: int | None
    capacity: float | None
    used_cores: float | None
    scope: str

    @property
    def percent(self) -> float | None:
        if self.used_cores is not None and self.capacity:
            return 100 * self.used_cores / self.capacity
        return None


@dataclass(frozen=True)
class MemoryUsage:
    used_gib: float
    total_gib: float
    scope: str

    @property
    def percent(self) -> float:
        return 100 * self.used_gib / self.total_gib


@dataclass(frozen=True)
class GpuUsage:
    index: int
    name: str
    utilization: float | None
    driver_sm: float | None
    memory_io: float | None
    memory_used_gib: float | None
    memory_total_gib: float | None
    sm_active: float | None = None
    sm_occupancy: float | None = None

    @property
    def memory_percent(self) -> float | None:
        if self.memory_used_gib is not None and self.memory_total_gib:
            return 100 * self.memory_used_gib / self.memory_total_gib
        return None


@dataclass(frozen=True)
class HardwareSnapshot:
    cpu: CpuUsage
    memory: MemoryUsage | None
    gpus: tuple[GpuUsage, ...]


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _number(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _cpu_topology() -> tuple[int | None, int | None]:
    physical = set()
    logical = with_topology = 0
    for block in _text(_PROC / "cpuinfo").split("\n\n"):
        fields = {key.strip(): value.strip() for key, _, value in (line.partition(":") for line in block.splitlines())}
        if "processor" not in fields:
            continue
        logical += 1
        if "physical id" in fields and "core id" in fields:
            with_topology += 1
            physical.add((fields["physical id"], fields["core id"]))
    cores = len(physical) if logical and with_topology == logical else None
    return cores, logical or os.cpu_count()


def _cgroup_paths() -> tuple[Path, ...]:
    membership = next((line[3:] for line in _text(_PROC / "self/cgroup").splitlines() if line.startswith("0::")), "")
    if not membership:
        return ()
    leaf = _CGROUP / membership.lstrip("/")
    if not (leaf / "cgroup.controllers").exists():
        return (_CGROUP,) if (_CGROUP / "cgroup.controllers").exists() else ()
    return (leaf, *(path for path in leaf.parents if path == _CGROUP or _CGROUP in path.parents))


def _cpu_quota(path: Path) -> float | None:
    parts = _text(path / "cpu.max").split()
    if len(parts) != 2:
        return None
    quota, period = map(_number, parts)
    return quota / period if quota and period else None


def _cpuset_count(path: Path) -> int | None:
    cpus = _text(path / "cpuset.cpus.effective").strip()
    if not cpus:
        return None
    count = 0
    for segment in cpus.split(","):
        first, _, last = segment.partition("-")
        if not first.isdigit() or (last and not last.isdigit()):
            return None
        count += int(last or first) - int(first) + 1
    return count or None


def _cgroup_cpu(path: Path) -> float | None:
    for line in _text(path / "cpu.stat").splitlines():
        key, _, value = line.partition(" ")
        if key == "usage_usec":
            usec = _number(value)
            return usec / 1e6 if usec is not None else None
    return None


def _host_cpu() -> tuple[float, float] | None:
    first, _, _ = _text(_PROC / "stat").partition("\n")
    fields = first.split()
    if len(fields) < 5 or fields[0] != "cpu":
        return None
    ticks = [int(value) for value in fields[1:9]]
    total = sum(ticks)
    idle = sum(ticks[3:5])
    return total - idle, total


def _memory_usage(paths: tuple[Path, ...]) -> MemoryUsage | None:
    limits = [(limit, path) for path in paths if (limit := _number(_text(path / "memory.max")))]
    if limits:
        limit, path = min(limits, key=lambda item: item[0])
        current = _number(_text(path / "memory.current"))
        if current is not None:
            return MemoryUsage(current / _GIB, limit / _GIB, "cgroup")
    values = {}
    for line in _text(_PROC / "meminfo").splitlines():
        key, _, value = line.partition(":")
        if key in {"MemTotal", "MemAvailable"}:
            values[key] = _number(value.split()[0])
    total, available = values.get("MemTotal"), values.get("MemAvailable")
    if total and available is not None:
        return MemoryUsage(max(0, total - available) / 1024**2, total / 1024**2, "host")
    return None


def _smi(executable: str, *args: str, timeout: float = 1.0) -> str:
    try:
        return subprocess.check_output([executable, *args], stderr=subprocess.DEVNULL, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""


def _dmon_values(output: str) -> dict[int, dict[str, float | None]]:
    headers = []
    devices = {}
    for row in csv.reader(output.splitlines(), skipinitialspace=True):
        if not row:
            continue
        if row[0].lstrip("# ").strip() == "gpu":
            headers = [cell.strip() for cell in row[1:]]
        elif headers and row[0].strip().isdigit():
            devices[int(row[0])] = {key: _number(value) for key, value in zip(headers, row[1:])}
    return devices


def _gpu_usage(executable: str) -> tuple[GpuUsage, ...]:
    query = _smi(
        executable,
        "--query-gpu=index,name,utilization.gpu,utilization.memory,memory.used,memory.total",
        "--format=csv,noheader,nounits",
    )
    rows = [row for row in csv.reader(query.splitlines(), skipinitialspace=True) if len(row) == 6 and row[0].isdigit()]
    if not rows:
        return ()
    indices = [int(row[0]) for row in rows]
    sm_counters = read_sm_counters(indices)
    if any(sm_counters.get(index, (None, None))[0] is None for index in indices):
        dmon = _dmon_values(_smi(executable, "dmon", "-s", "u", "--format", "csv,nounit", "-c", "1"))
    else:
        dmon = {}
    devices = []
    for index, row in zip(indices, rows):
        util, mem_io, used_mib, total_mib = map(_number, row[2:])
        active, occupancy = sm_counters.get(index, (None, None))
        devices.append(
            GpuUsage(
                index,
                row[1],
                util,
                dmon.get(index, {}).get("sm"),
                mem_io,
                used_mib / 1024 if used_mib is not None else None,
                total_mib / 1024 if total_mib is not None else None,
                active,
                occupancy,
            )
        )
    return tuple(devices)


class HardwareMonitor:
    """Sample interval CPU use and reuse the GPU reading for rapid terminal redraws."""

    def __init__(self) -> None:
        self._physical, self._logical = _cpu_topology()
        self._paths = _cgroup_paths()
        quotas = [(quota, path) for path in self._paths if (quota := _cpu_quota(path)) is not None]
        self._quota, self._cpu_path = min(quotas, key=lambda item: item[0]) if quotas else (None, None)
        if self._paths and self._cpu_path is None:
            cpus = _cpuset_count(self._paths[0])
            if cpus and self._logical and cpus < self._logical:
                self._quota, self._cpu_path = float(cpus), self._paths[0]
        if self._cpu_path is not None and (cpus := _cpuset_count(self._cpu_path)):
            self._quota = min(self._quota, cpus)
        self._previous: tuple[str, float, float] | None = None
        self._executable = shutil.which("nvidia-smi")
        self._gpu_sampled = -math.inf
        self._gpus: tuple[GpuUsage, ...] = ()

    def _cpu_counters(self) -> tuple[str, float, float] | None:
        if self._cpu_path is not None and (value := _cgroup_cpu(self._cpu_path)) is not None:
            return "cgroup", value, time.monotonic()
        host = _host_cpu()
        return None if host is None else ("host", *host)

    def read(self) -> HardwareSnapshot:
        """Collect CPU, memory and all locally visible NVIDIA GPUs."""
        previous = self._previous or self._cpu_counters()
        now = time.monotonic()
        if self._executable and now - self._gpu_sampled >= 1.0:
            self._gpus = _gpu_usage(self._executable)
            self._gpu_sampled = time.monotonic()
        memory = _memory_usage(self._paths)
        if self._previous is None and previous is not None:
            time.sleep(max(0, 0.12 - (time.monotonic() - now)))
        current = self._previous = self._cpu_counters()
        scope = current[0] if current else "host"
        capacity = self._quota if scope == "cgroup" else self._logical
        used = None
        if previous and current and previous[0] == current[0] and capacity:
            busy, duration = current[1] - previous[1], current[2] - previous[2]
            if busy >= 0 and duration > 0:
                used = busy / duration * (1 if scope == "cgroup" else capacity)
        return HardwareSnapshot(CpuUsage(self._physical, self._logical, capacity, used, scope), memory, self._gpus)


@dataclass(frozen=True)
class HardwareMeter:
    label: str
    utilization: float | None
    color: str
    detail: str = ""

    @property
    def fraction(self) -> float | None:
        if self.utilization is None or not math.isfinite(self.utilization):
            return None
        return min(1.0, max(0.0, self.utilization / 100.0))


def _capacity(used: float | None, total: float | None, unit: str) -> str:
    used_text = "--" if used is None else f"{used:.1f}"
    total_text = "--" if total is None else f"{total:.1f}" if unit == "GiB" else f"{total:g}"
    return f"{used_text}/{total_text} {unit}".rstrip()


def hardware_meters(snapshot: HardwareSnapshot, *, ascii_only: bool = False) -> list[HardwareMeter]:
    """Share the load, capacity notes and color of each bar across renderers."""
    cpu = snapshot.cpu
    physical = cpu.physical_cores if cpu.physical_cores is not None else "--"
    logical = cpu.logical_cpus if cpu.logical_cpus is not None else "--"
    cpu_scope = (
        ("quota" if cpu.scope == "cgroup" else "host") if ascii_only else ("配额" if cpu.scope == "cgroup" else "整机")
    )
    cores = _capacity(cpu.used_cores, cpu.capacity, "" if ascii_only else "核")
    topology = f"{physical}C/{logical}T" if ascii_only else f"{physical} 物理核 / {logical} 线程"
    separator = " | " if ascii_only else " · "
    meters = [HardwareMeter("CPU", cpu.percent, "#66c7ed", f"{cores} {cpu_scope}{separator}{topology}")]
    ram = snapshot.memory
    ram_detail = ""
    if ram is not None:
        scope = (
            ("quota" if ram.scope == "cgroup" else "host")
            if ascii_only
            else ("配额" if ram.scope == "cgroup" else "整机")
        )
        ram_detail = f"{_capacity(ram.used_gib, ram.total_gib, 'GiB')} {scope}"
    meters.append(HardwareMeter("RAM" if ascii_only else "内存", ram.percent if ram else None, "#7edcbb", ram_detail))
    for gpu in snapshot.gpus:
        prefix = f"GPU {gpu.index}"
        active = gpu.sm_active
        sm_label = "SM Active" if ascii_only else "SM 活跃"
        if active is None and gpu.driver_sm is not None:
            active = gpu.driver_sm
            sm_label = "SM (driver)" if ascii_only else "SM 常规"
        meters.extend(
            (
                HardwareMeter(f"{prefix} {'Busy' if ascii_only else '忙碌'}", gpu.utilization, "#a69bf5", gpu.name),
                HardwareMeter(f"{prefix} {sm_label}", active, "#a69bf5"),
                HardwareMeter(
                    f"{prefix} {'VRAM' if ascii_only else '显存'}",
                    gpu.memory_percent,
                    "#7edcbb",
                    _capacity(gpu.memory_used_gib, gpu.memory_total_gib, "GiB"),
                ),
                HardwareMeter(f"{prefix} {'Warp' if ascii_only else 'Warp 占用'}", gpu.sm_occupancy, "#a69bf5"),
                HardwareMeter(f"{prefix} {'VRAM I/O' if ascii_only else '显存读写'}", gpu.memory_io, "#7edcbb"),
            )
        )
    if not snapshot.gpus:
        meters.append(HardwareMeter("GPU", None, "#a69bf5"))
    return meters
