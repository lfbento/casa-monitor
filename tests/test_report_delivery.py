from __future__ import annotations

import logging

import main


def _report_file(tmp_path):
    f = tmp_path / "relatorio.md"
    f.write_text("# t\n## a\n## b\n## c\n## d")
    return f


def test_scheduled_report_logs_failure_not_success(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr("report.daily_report.generate_report", lambda store: _report_file(tmp_path))
    monkeypatch.setattr("notifier.send", lambda text: False)
    with caplog.at_level(logging.ERROR):
        main.run_scheduled_report(store=None)
    assert any("ENTREGA FALHOU" in r.message for r in caplog.records)
    assert not any("enviado" in r.message for r in caplog.records)


def test_scheduled_report_logs_success_when_delivered(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr("report.daily_report.generate_report", lambda store: _report_file(tmp_path))
    monkeypatch.setattr("notifier.send", lambda text: True)
    with caplog.at_level(logging.INFO):
        main.run_scheduled_report(store=None)
    assert any("enviado" in r.message for r in caplog.records)


def test_cli_returns_nonzero_on_send_failure(monkeypatch, tmp_path):
    monkeypatch.setattr("db.store.Store", lambda: object())
    monkeypatch.setattr("report.daily_report.generate_report", lambda store: _report_file(tmp_path))
    monkeypatch.setattr("notifier.send", lambda text: False)
    assert main.generate_report_now(send=True) == 1


def test_cli_returns_zero_without_send(monkeypatch, tmp_path):
    monkeypatch.setattr("db.store.Store", lambda: object())
    monkeypatch.setattr("report.daily_report.generate_report", lambda store: _report_file(tmp_path))
    assert main.generate_report_now(send=False) == 0
