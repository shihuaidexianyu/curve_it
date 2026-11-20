from pathlib import Path

from curve_it.export import save_html, save_png
from curve_it.log import parse_console_log
from curve_it.metrics import default_panels
from curve_it.terminal import render_braille_box

_SAMPLE_LOG = """
################################################################################
                      Learning iteration 0/4000

                       Computation: 40100 steps/s (collection: 9.4s, learning 0.4s)
             Mean action noise std: 0.26
                 Mean mean_kl loss: 0.0090
               Mean critic_ev loss: 0.9000
           Mean kl_early_stop loss: 0.0000
                       Mean reward: -4.20
               Mean episode length: 78.00
      Episode_Reward/progress_best: 0.0120
      Episode_Reward/feet_air_time: 0.0005
          Episode_Termination/fall: 1.0000
                 Loco/track_kernel: 0.0100
                  Curriculum/level: 0.0000
                    Iteration time: 9.80s
                      Time elapsed: 00:00:10
                               ETA: 10:50:00
################################################################################
                      Learning iteration 50/4000

                       Computation: 41200 steps/s (collection: 9.1s, learning 0.4s)
             Mean action noise std: 0.25
                 Mean mean_kl loss: 0.0085
               Mean critic_ev loss: 0.9800
           Mean kl_early_stop loss: 0.0000
                       Mean reward: 5.40
               Mean episode length: 240.00
      Episode_Reward/progress_best: 0.3500
      Episode_Reward/feet_air_time: 0.0250
          Episode_Termination/fall: 0.3500
                 Loco/track_kernel: 0.2800
                  Curriculum/level: 0.0000
                    Iteration time: 9.50s
                      Time elapsed: 00:08:20
                               ETA: 10:25:00
"""


def test_parse_braille_html_and_png(tmp_path: Path):
    log_file = tmp_path / "be2e_s1_test.log"
    log_file.write_text(_SAMPLE_LOG, encoding="utf-8")

    parsed = parse_console_log(str(log_file))
    assert parsed.iters == [0, 50]
    assert parsed.max_iters == 4000
    assert parsed.latest("Mean reward") == 5.40
    assert parsed.latest("Episode_Reward/progress_best") == 0.35

    panels = default_panels(parsed)
    assert len(panels) == 6
    lines_col = render_braille_box(parsed.series["Mean reward"], "平均总回报", color=True)
    assert len(lines_col) >= 8 and "\033[" in lines_col[1]
    lines_plain = render_braille_box(parsed.series["Mean reward"], "平均总回报", color=False)
    assert "\033[" not in "".join(lines_plain)

    html_out = tmp_path / "curve.html"
    save_html(parsed, panels, str(html_out))
    assert "<svg" in html_out.read_text(encoding="utf-8")

    png_out = tmp_path / "curve.png"
    save_png(parsed, panels, str(png_out))
    assert png_out.exists() and png_out.stat().st_size > 5000


def test_ascii_view_and_partial_iteration(tmp_path, monkeypatch, capsys):
    from curve_it.cli import main

    log_file = tmp_path / "be2e_test.log"
    log_file.write_text(_SAMPLE_LOG + "\x1b[1mLearning iteration 51/4000\x1b[0m\nMean reward: 7")
    monkeypatch.setenv("TERM", "linux")
    assert main([str(log_file)]) == 0
    output = capsys.readouterr().out
    assert output.isascii()
    assert "Iteration 51/4000" in output
    assert "5.400" in output
    with log_file.open("a") as stream:
        stream.write("0\n")
    assert parse_console_log(log_file).latest("Mean reward") == 70.0


def test_export_will_not_write_over_input(tmp_path):
    import pytest

    from curve_it.cli import main

    log_file = tmp_path / "be2e_test.log"
    log_file.write_text(_SAMPLE_LOG)
    same_file = tmp_path / "same-file.html"
    same_file.symlink_to(log_file)
    with pytest.raises(SystemExit) as raised:
        main([str(log_file), "--html", str(same_file)])
    assert raised.value.code == 1
    assert log_file.read_text() == _SAMPLE_LOG
