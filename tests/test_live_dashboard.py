import os
from threading import Event

import pytest

from curve_it import LogReader, ParsedLog, render_dashboard, select_panels
from curve_it.cli import main
from curve_it.live import _read_key, screen_update, watch
from curve_it.terminal import DashboardFrame, _vis_width


def test_reader_follows_complete_lines_and_restarts(tmp_path):
    path = tmp_path / "run.log"
    path.write_bytes(b"Learning iteration 6/10\nMean reward: 1")
    reader = LogReader(path)
    assert reader.read().latest("Mean reward") is None
    revision = reader.revision
    assert reader.read().iters == [6]
    assert reader.revision == revision
    with path.open("ab") as sink:
        sink.write(b".5\nLearning iteration 6/10\nMean reward: 2\n")
    assert reader.read().series["Mean reward"] == [(6, 2.0)]
    path.write_bytes(b"Learning iteration 7/10\nMean reward: 8\n" + b"x" * 128 + b"\n")
    assert reader.read().series["Mean reward"] == [(7, 8.0)]
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"Learning iteration 8/20\nMean reward: 15\n")
    replacement.replace(path)
    assert reader.read().series["Mean reward"] == [(8, 15.0)]
    with path.open("ab") as sink:
        sink.write(b"Learning iteration 0/5\nMean reward: -3\n")
    assert reader.read().series["Mean reward"] == [(0, -3.0)]


def test_live_key_reader_keeps_arrows_and_escape_distinct(monkeypatch):
    chunks = [b"\x1b", b"[C\x1bOB\r\x1b[1;5A\x1b"]
    monkeypatch.setattr("curve_it.live.select.select", lambda *args: ([0] if chunks else [], [], []))
    monkeypatch.setattr("curve_it.live.os.read", lambda *args: chunks.pop(0))
    pending = bytearray()

    keys = [_read_key(0, pending, 0) for _ in range(6)]

    assert keys == ["right", "down", "\r", "", "escape", None]


def test_live_navigation_redraws_while_refresh_is_pending(monkeypatch, capsys):
    termios = pytest.importorskip("termios")
    started, release = Event(), Event()
    drawn = []
    keys = iter(("right", "q"))

    def refresh():
        started.set()
        assert release.wait(2)
        return "updated"

    def render(data, size, *, page, **kwargs):
        drawn.append((data, page))
        return DashboardFrame((f"page {page + 1}",), page, 2)

    def read_key(*args):
        assert started.wait(2)
        key = next(keys)
        if key == "q":
            release.set()
        return key

    monkeypatch.setattr("sys.stdin.fileno", lambda: 0)
    monkeypatch.setattr(termios, "tcgetattr", lambda fd: None)
    monkeypatch.setattr(termios, "tcsetattr", lambda *args: None)
    monkeypatch.setattr("tty.setcbreak", lambda fd: None)
    monkeypatch.setattr("curve_it.live._read_key", read_key)
    watch(refresh, render, 5, True, "current")

    assert drawn == [("current", 0), ("current", 1)]
    assert "page 2" in capsys.readouterr().out


def test_real_metric_presets_and_viewport():
    log = ParsedLog(
        "be2e_s2cd_forehand_v7.log",
        [12],
        400,
        {
            "Hit/receive_legal_rate": [(12, 0.37)],
            "Jump/airborne_hit_rate": [(12, 0.03)],
            "Diag/peak_speed_mps": [(12, 1.8)],
            "Hit/forehand_rate": [(12, 0.61)],
        },
    )
    profile, panels = select_panels(log)
    assert profile.name == "athletic"
    assert [panel.format(log.latest(panel.key)) for panel in panels] == ["37.0%", "1.800 m/s", "3.0%", "61.0%"]
    for width, height, ascii_only in ((104, 24, False), (72, 12, False), (40, 5, True)):
        frame = render_dashboard(
            log, panels, profile.title, width=width, height=height, ascii_only=ascii_only, watching=True
        )
        assert len(frame.lines) <= height
        assert all(_vis_width(line) <= width for line in frame.lines)
        if ascii_only:
            assert all(line.isascii() for line in frame.lines)
            assert frame.pages == 2
            next_frame = render_dashboard(
                log, panels, profile.title, width=width, height=height, ascii_only=True, watching=True, page=1
            )
            patch = screen_update(frame.lines, next_frame.lines)
            assert "61.0%" in patch and "Iteration" not in patch
    assert screen_update(frame.lines, frame.lines) == ""


def test_directory_watch_switches_logs_and_checks_exports(tmp_path, monkeypatch):
    first = tmp_path / "be2e_s1.log"
    second = tmp_path / "be2e_s3_physx.log"
    report = tmp_path / "report.html"
    first.write_text("Learning iteration 1/9\nMean reward: 4\n")

    def drive_watch(refresh, render, interval, interactive, initial):
        size = os.terminal_size((80, 24))
        first_data = refresh()
        assert "be2e_s1.log" in render(first_data, size).lines[0]
        first_report = report.stat().st_ino
        with first.open("a") as stream:
            stream.write("Mean reward: 7")
        refresh()
        assert report.stat().st_ino == first_report
        second.write_text("Learning iteration 2/9\nSP/win_vs_base: 0.72\n")
        os.utime(second, ns=(first.stat().st_mtime_ns + 1_000_000, first.stat().st_mtime_ns + 1_000_000))
        assert "be2e_s3_physx.log" in render(refresh(), size).lines[0]
        html = report.read_text()
        assert "战术自对弈" in html and "72.0%" in html

    monkeypatch.setattr("curve_it.cli.watch", drive_watch)
    assert main([str(tmp_path), "--watch", "1", "--html", str(report), "--no-hardware"]) == 0


def test_dense_layout_retains_one_tick_spike_and_fits_more_visible_charts():
    points = [(i, float(i == 485)) for i in range(501)]
    log = ParsedLog("training.log", list(range(501)), 700, {f"Test/metric_{i}": points for i in range(28)})
    profile, panels = select_panels(log, dense=True, all_metrics=True)
    frame = render_dashboard(log, panels, profile.title, width=108, height=36, watching=True, dense=True)
    assert frame.pages == 1
    assert len(frame.lines) <= 36 and all(_vis_width(line) <= 108 for line in frame.lines)
    assert "第 381–500 轮" in "\n".join(frame.lines)
    assert "28/28 charts" in frame.lines[-1]
    assert len(panels) == 28
    # A single-step peak falls between screen columns and still changes the rendered trace.
    for key in log.series:
        log.series[key] = [(i, 0.0) for i in range(501)]
    flat = render_dashboard(log, panels, profile.title, width=108, height=36, watching=True, dense=True)
    assert frame.lines != flat.lines
    narrow = render_dashboard(
        log, panels, profile.title, width=60, height=10, watching=True, dense=True, ascii_only=True
    )
    assert narrow.pages > 1 and len(narrow.lines) <= 10
    assert all(line.isascii() for line in narrow.lines)


def test_watch_keeps_four_chart_pages_and_opens_single_chart_with_hardware(tmp_path, monkeypatch):
    from curve_it.hardware import CpuUsage, GpuUsage, HardwareSnapshot, MemoryUsage

    source = tmp_path / "be2e_s2c_ladder.log"
    keys = [f"Metric/trend_{index}" for index in range(13)]
    source.write_text(
        "Learning iteration 0/50\n"
        + "".join(f"{key}: 0\n" for key in keys)
        + "Learning iteration 32/50\n"
        + "".join(f"{key}: {index + 1}\n" for index, key in enumerate(keys))
    )
    snapshot = HardwareSnapshot(
        CpuUsage(90, 180, 20, 8.5, "cgroup"),
        MemoryUsage(48, 200, "cgroup"),
        (GpuUsage(0, "Accelerator X", 72, None, 17, 16, 32, 45, 28),),
    )
    monkeypatch.setattr("curve_it.cli.HardwareMonitor.read", lambda self: snapshot)

    def drive_watch(refresh, render, interval, interactive, initial):
        size = os.terminal_size((132, 41))
        data = refresh()
        frame = render(data, size, viewport=True)
        assert frame.pages == 4 and frame.keys == tuple(keys[:4])
        assert "四图 1/4" in frame.lines[-1] and "←→ 翻页" in frame.lines[-1]
        assert any(line.startswith("CPU") for line in frame.lines)
        axis_top = next(i for i, line in enumerate(frame.lines) if "┐" in line)
        axis_bottom = next(i for i, line in enumerate(frame.lines) if "└" in line)
        assert axis_bottom - axis_top + 1 >= 10
        assert len(frame.lines) <= size.lines and all(_vis_width(line) <= size.columns for line in frame.lines)
        detail = render(data, size, viewport=True, selected_key=keys[1], focused=True)
        assert "单图 2/13" in detail.lines[-1] and detail.keys == (keys[1],)
        assert keys[1] in "\n".join(detail.lines) and keys[0] not in "\n".join(detail.lines)
        assert max(map(_vis_width, detail.lines[:-1])) > size.columns * 0.9
        next_detail = render(data, size, viewport=True, page=4, focused=True)
        assert next_detail.keys == (keys[4],)
        grid = render(data, os.terminal_size((48, 20)), viewport=True, selected_key=next_detail.selected_key)
        assert grid.page == 1 and grid.keys == tuple(keys[4:8]) and grid.selected_key == keys[4]
        assert all(_vis_width(line) <= 48 for line in grid.lines)
        last = render(data, size, viewport=True, page=3)
        assert last.keys == (keys[12],)
        assert "Metric/trend_12" in "\n".join(last.lines)

    monkeypatch.setattr("curve_it.cli.watch", drive_watch)
    assert main([str(source), "--watch", "1", "--all-metrics", "--unicode"]) == 0
