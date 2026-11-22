from curve_it import ParsedLog, render_dashboard, select_panels
from curve_it.cli import main
from curve_it.hardware import CpuUsage, GpuUsage, HardwareSnapshot, MemoryUsage, _gpu_usage
from curve_it.terminal import _vis_width


def test_gpu_metrics_keep_busy_sm_and_vram_distinct(monkeypatch):
    def smi(executable, *args):
        if args[0].startswith("--query-gpu"):
            return "0, Accelerator A, 81, 22, 16384, 32768\n1, Accelerator B, 36, 17, 2048, 16384\n"
        return "# gpu, sm, mem\n0, 80, 21\n1, 34, 17\n"

    monkeypatch.setattr("curve_it.hardware._smi", smi)
    monkeypatch.setattr("curve_it.hardware.read_sm_counters", lambda indices: {0: (64.0, 25.0)})
    devices = _gpu_usage("nvidia-smi")
    assert devices[0].utilization == 81.0
    assert devices[0].sm_active == 64.0
    assert devices[0].sm_occupancy == 25.0
    assert devices[0].memory_percent == 50.0
    assert devices[0].memory_io == 22.0
    assert devices[1].sm_active is None
    assert devices[1].driver_sm == 34.0


def test_hardware_fits_a_compact_viewport():
    log = ParsedLog("run.log", [1], 10, {"Mean reward": [(1, 2.0)]})
    profile, panels = select_panels(log)
    snapshot = HardwareSnapshot(
        CpuUsage(90, 180, 20, 8.5, "cgroup"),
        MemoryUsage(48, 200, "cgroup"),
        (GpuUsage(0, "Accelerator X", 72, None, 17, 16, 32, 45, 28),),
    )
    frame = render_dashboard(
        log, panels, profile.title, width=60, height=10, ascii_only=True, watching=True, hardware=snapshot
    )
    joined = "\n".join(frame.lines)
    assert len(frame.lines) <= 10 and all(_vis_width(line) <= 60 for line in frame.lines)
    for text in (
        "8.5/20 quota | 90C/180T",
        "48.0/200.0 GiB quota",
        "GPU 0 Busy",
        "GPU 0 SM Active",
        "GPU 0 VRAM",
        "GPU 0 Warp",
    ):
        assert text in joined
    assert "%" not in joined
    for label, track in (
        ("CPU", "#####+-------"),
        ("RAM", "###----------"),
        ("GPU 0 Busy", "#########+---"),
        ("GPU 0 VRAM", "######+------"),
    ):
        assert f"[{track}]" in next(line for line in frame.lines if line.startswith(label))


def test_watch_html_refreshes_hardware_while_log_is_idle(tmp_path, monkeypatch):
    log = tmp_path / "training.log"
    log.write_text("Learning iteration 1/2\nMean reward: 4\n")
    report = tmp_path / "training.html"
    reads = iter(
        [
            HardwareSnapshot(CpuUsage(12, 24, 10, used, "cgroup"), MemoryUsage(40, 200, "cgroup"), ())
            for used in (3.0, 5.0)
        ]
    )
    monkeypatch.setattr("curve_it.cli.HardwareMonitor.read", lambda self: next(reads))

    def watch(refresh, render, interval, interactive, initial):
        for used in (30, 50):
            refresh()
            html = report.read_text()
            assert f'aria-valuenow="{used}"' in html
            assert f"inline-size:{used}%" in html
            assert f">{used}%" not in html
            assert "meter-track missing" in html

    monkeypatch.setattr("curve_it.cli.watch", watch)
    assert main([str(log), "--watch", "1", "--html", str(report)]) == 0
