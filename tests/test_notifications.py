import io
import json

import pytest

from curve_it import Notification, NotificationError, notifier_from_env
from curve_it.cli import main


@pytest.mark.parametrize(
    ("key", "endpoint", "tagged"),
    [("sctp123tFake", "123.push.ft07.com/send/", True), ("SCTFake", "sctapi.ftqq.com/", False)],
)
def test_serverchan_formats_both_api_variants(monkeypatch, key, endpoint, tagged):
    def receive(request, timeout):
        assert timeout == 10
        assert request.full_url == f"https://{endpoint}{key}.send"
        assert request.get_method() == "POST"
        assert request.get_header("Content-type") == "application/json; charset=utf-8"
        fields = json.loads(request.data)
        assert fields["title"] == ("训练" if tagged else "[curve] 训练")
        assert fields["desp"] == "**正文**" and fields["short"] == "Iteration 123"
        assert fields.get("tags") == ("curve" if tagged else None)
        return io.BytesIO(b'{"code":0}')

    monkeypatch.setattr("curve_it.notifications.urlopen", receive)
    notifier_from_env({"SERVERCHAN_SENDKEY": key}).send(Notification("训练", "**正文**", "Iteration 123"))


def test_delivery_failure_does_not_expose_token(monkeypatch):
    def fail(request, timeout):
        raise OSError(request.full_url)

    monkeypatch.setattr("curve_it.notifications.urlopen", fail)
    with pytest.raises(NotificationError, match="connection") as caught:
        notifier_from_env({"SERVERCHAN_SENDKEY": "SCTFakeSecret"}).send(Notification("title", "report"))
    assert "SCTFakeSecret" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_cli_sends_only_when_due_with_new_metrics(tmp_path, monkeypatch):
    log = tmp_path / "run.log"
    log.write_text("Learning iteration 10/50\nMean mean_kl loss: 0.019\n")
    now = [0]
    messages = []
    monkeypatch.setenv("SERVERCHAN_SENDKEY", "SCTFake")
    monkeypatch.setenv("CURVE_IT_NOTIFY_PROVIDER", "serverchan")
    monkeypatch.setenv("CURVE_IT_CHART_URL", "https://charts.example.test/run.png")
    monkeypatch.setattr("curve_it.reports.time.monotonic", lambda: now[0])

    def receive(request, timeout):
        messages.append(json.loads(request.data))
        return io.BytesIO(b'{"code":0}')

    def watch(refresh, render, interval, interactive, initial):
        assert interval == 5
        refresh()
        assert not messages
        now[0] = 600
        refresh()
        now[0] = 1200
        refresh()

    monkeypatch.setattr("curve_it.notifications.urlopen", receive)
    monkeypatch.setattr("curve_it.cli.watch", watch)
    assert main([str(log), "--notify-every", "10m", "--no-hardware"]) == 0
    assert len(messages) == 1
    assert messages[0]["short"] == "Iteration 10/50"
    assert "**0.0190**" in messages[0]["desp"]
    assert "![训练曲线](<https://charts.example.test/run.png>)" in messages[0]["desp"]
