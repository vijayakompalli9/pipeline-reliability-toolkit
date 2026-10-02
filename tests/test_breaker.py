import json

import pandas as pd
import pytest

from reliability import breaker as breaker_mod
from reliability.breaker import EXIT_CIRCUIT_OPEN, EXIT_OK, CircuitBreaker, build_alert, send_alert
from reliability.cli import main
from reliability.exceptions import CircuitOpenError
from reliability.results import CheckResult, Severity


def res(passed, severity, check="x"):
    return CheckResult(check, "quality", "ds", passed, Severity(severity), message="m")


def test_closed_when_only_warnings():
    d = CircuitBreaker().evaluate([res(True, "critical"), res(False, "warn")])
    assert not d.is_open and d.exit_code == EXIT_OK and len(d.warnings) == 1


def test_open_on_critical_failure():
    d = CircuitBreaker().evaluate([res(False, "critical", "unique"), res(False, "warn")])
    assert d.is_open and d.exit_code == EXIT_CIRCUIT_OPEN and [r.check for r in d.blocking] == ["unique"]


def test_fail_on_warn_blocks_warnings():
    assert CircuitBreaker(Severity.WARN).evaluate([res(False, "warn")]).exit_code == EXIT_CIRCUIT_OPEN


def test_guard_raises_with_decision():
    with pytest.raises(CircuitOpenError) as exc:
        CircuitBreaker().guard([res(False, "critical")])
    assert exc.value.decision.is_open


@pytest.mark.parametrize("fmt, key", [("slack", "blocks"), ("teams", "attachments"), ("pagerduty", "routing_key")])
def test_alert_shapes(fmt, key):
    d = CircuitBreaker().evaluate([res(False, "critical")])
    payload = build_alert(d, "ds", "run-1", fmt)
    assert key in payload
    json.dumps(payload)  # serializable
    if fmt == "pagerduty":
        assert payload["event_action"] == "trigger" and payload["payload"]["severity"] == "critical"


def test_unknown_alert_format():
    with pytest.raises(ValueError):
        build_alert(CircuitBreaker().evaluate([]), "ds", "r", "email")


def test_send_alert_dry_run_by_default(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network must not be used")

    monkeypatch.setattr(breaker_mod.urllib.request, "urlopen", boom)
    monkeypatch.setenv("RELIABILITY_WEBHOOK_URL", "https://hooks.example.invalid/x")
    monkeypatch.delenv("RELIABILITY_ALERT_DRY_RUN", raising=False)
    assert send_alert({"text": "hi"}) == {"sent": False, "dry_run": True, "bytes": 14}


@pytest.fixture
def csvs(tmp_path):
    good = pd.DataFrame({"order_id": ["A", "B"], "qty": [1, 2]})
    bad = pd.DataFrame({"order_id": ["A", "A"], "qty": [1, 2]})
    good.to_csv(tmp_path / "good.csv", index=False)
    bad.to_csv(tmp_path / "bad.csv", index=False)
    (tmp_path / "c.yaml").write_text(
        "dataset: orders\nschema:\n  - {name: order_id, type: string, nullable: false}\n"
        "  - {name: qty, type: integer}\nrules:\n  - {type: unique, column: order_id, severity: critical}\n"
    )
    return tmp_path


def test_cli_exit_codes(csvs, capsys):
    assert main(["check", "--contract", str(csvs / "c.yaml"), "--data", str(csvs / "good.csv")]) == 0
    assert main(["check", "--contract", str(csvs / "c.yaml"), "--data", str(csvs / "bad.csv"),
                 "--out-dir", str(csvs / "out"), "--alert-format", "pagerduty"]) == 2
    assert json.loads((csvs / "out" / "alert_pagerduty.json").read_text())["event_action"] == "trigger"
    assert main(["check", "--contract", str(csvs / "missing.yaml"), "--data", "x.csv"]) == 3
    assert "circuit=open" in capsys.readouterr().out
