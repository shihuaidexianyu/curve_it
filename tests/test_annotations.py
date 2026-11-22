import pytest

from curve_it import (
    AnnotationError,
    Panel,
    ParsedLog,
    PointLabel,
    ReferenceBand,
    ReferenceLine,
    load_annotations,
    render_dashboard,
    validate_annotation_keys,
)
from curve_it.export import save_html


def test_annotation_configuration_reports_field_errors(tmp_path):
    config = tmp_path / "annotations.json"
    config.write_text(
        '{"Metric/A":[{"type":"line","value":"2"},{"type":"band","lower":4,"upper":3,"label":"band"}]}'
    )
    with pytest.raises(AnnotationError) as caught:
        load_annotations(config)
    assert {issue.path.rsplit(".", 1)[-1] for issue in caught.value.issues} == {"value", "label", "upper"}
    assert "must exceed lower (4)" in str(caught.value)
    with pytest.raises(AnnotationError, match=r"0\.90 means 90%"):
        Panel("Hit/receive_legal_rate", "Legal return", "%", annotations=(ReferenceLine(90, "Target"),))
    with pytest.raises(AnnotationError, match="Did you mean 'Metric/A'"):
        validate_annotation_keys({"Metrik/A": ()}, ["Metric/A"])


def test_annotations_extend_axes_and_render_safely(tmp_path):
    log = ParsedLog("run.log", [1, 2, 3], 80, {"height": [(1, 1.1), (2, 1.3), (3, 1.5)]})
    panel = Panel(
        "height", "Height", "m",
        annotations=(ReferenceLine(2, "Target <height>"), ReferenceBand(1.1, 1.3, "Range"), PointLabel(2, 1.3, "Hit")),
    )
    terminal = "\n".join(render_dashboard(log, [panel], "Training", width=75, height=22, ascii_only=True).lines)
    assert "2.000 +" in terminal and "Target <height>" in terminal and "Range" in terminal and "@" in terminal
    dest = tmp_path / "report.html"
    save_html(log, [panel], str(dest))
    assert "Target &lt;height&gt;" in dest.read_text()
