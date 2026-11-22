"""Read NVIDIA's SM activity and warp occupancy counters through NVML GPM."""

from __future__ import annotations

import math
import time
from ctypes import CDLL, Structure, byref, c_char_p, c_double, c_int, c_uint, c_void_p

_SM_METRICS = (2, 3)  # NVML_GPM_METRIC_SM_UTIL and NVML_GPM_METRIC_SM_OCCUPANCY
_GPM_VERSION = 1
_GPM_METRIC_MAX = 98  # Size of the fixed metric array in nvmlGpmMetricsGet_t v1.


class _DeviceSupport(Structure):
    _fields_ = [("version", c_uint), ("is_supported", c_uint)]


class _MetricInfo(Structure):
    _fields_ = [("short_name", c_char_p), ("long_name", c_char_p), ("unit", c_char_p)]


class _Metric(Structure):
    _fields_ = [("id", c_uint), ("status", c_int), ("value", c_double), ("info", _MetricInfo)]


class _MetricRequest(Structure):
    _fields_ = [
        ("version", c_uint),
        ("count", c_uint),
        ("first", c_void_p),
        ("second", c_void_p),
        ("metrics", _Metric * _GPM_METRIC_MAX),
    ]


def read_sm_counters(indices: list[int]) -> dict[int, tuple[float | None, float | None]]:
    """Sample all supported devices over a shared interval using the driver's read API."""
    try:
        nvml = CDLL("libnvidia-ml.so.1")
        init, shutdown = nvml.nvmlInit_v2, nvml.nvmlShutdown
        get_device, supports_gpm = nvml.nvmlDeviceGetHandleByIndex_v2, nvml.nvmlGpmQueryDeviceSupport
        allocate, free = nvml.nvmlGpmSampleAlloc, nvml.nvmlGpmSampleFree
        sample, get_metrics = nvml.nvmlGpmSampleGet, nvml.nvmlGpmMetricsGet
    except (OSError, AttributeError):
        return {}
    if init() != 0:
        return {}

    allocated = []
    devices = []
    try:
        for index in indices:
            device = c_void_p()
            support = _DeviceSupport(_GPM_VERSION)
            if get_device(c_uint(index), byref(device)) != 0:
                continue
            if supports_gpm(device, byref(support)) != 0 or not support.is_supported:
                continue
            first, second = c_void_p(), c_void_p()
            if allocate(byref(first)) != 0:
                continue
            allocated.append(first)
            if allocate(byref(second)) != 0:
                continue
            allocated.append(second)
            if sample(device, first) == 0:
                devices.append((index, device, first, second))
        if not devices:
            return {}
        time.sleep(0.15)

        counters = {}
        for index, device, first, second in devices:
            if sample(device, second) != 0:
                continue
            request = _MetricRequest(_GPM_VERSION, len(_SM_METRICS), first, second)
            for metric, metric_id in zip(request.metrics, _SM_METRICS):
                metric.id = metric_id
            if get_metrics(byref(request)) == 0:
                activity, occupancy = request.metrics[:2]
                counters[index] = (_value(activity), _value(occupancy))
        return counters
    finally:
        for buffer in allocated:
            free(buffer)
        shutdown()


def _value(metric: _Metric) -> float | None:
    if metric.status == 0 and math.isfinite(metric.value) and 0 <= metric.value <= 100:
        return metric.value
    return None
