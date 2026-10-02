import importlib.util
import json
from pathlib import Path

import pytest

from reliability import demo
from reliability.exceptions import CircuitOpenError

ROOT = Path(__file__).resolve().parents[1]


def _by_check(results):
    return {(r["check"], r["column"]): r for r in results}


def test_demo_end_to_end(tmp_path):
    out, data = tmp_path / "out", tmp_path / "data"
    assert demo.run(out, data, ROOT / "configs") == 2  # circuit opens on injected defects
    assert {p.name for p in out.iterdir()} == {
        "results.json", "report.md", "incident_note.md", "alert_slack.json", "alert_teams.json",
        "alert_pagerduty.json"}
    payload = json.loads((out / "results.json").read_text())
    assert payload["circuit_state"] == "open" and payload["exit_code"] == 2
    res = _by_check(payload["results"])

    drift = res[("column_type_changed", "branch_code")]
    assert drift["severity"] == "critical" and drift["details"]["breaking"]
    assert res[("freshness", "event_ts")]["observed"]["age_hours"] == 30.0
    assert res[("rejected_accounting", None)]["observed"] == {"missing_keys": 170, "rejected_rows": 20,
                                                             "unaccounted": 150}  # 3% of 5000
    assert res[("duplicates", "payment_id")]["observed"]["extra_rows"] == 25
    assert not res[("control_total", "amount")]["passed"]
    assert res[("referential", "policy_id")]["observed"]["orphan_keys"] == 6
    assert res[("unique", "payment_id")]["passed"]  # source itself is clean

    note = (out / "incident_note.md").read_text()
    assert "Upstream schema change" in note and "Failed-then-retried load" in note
    assert "rpt_statutory_cash_flow" in note

    # Determinism: a second run with the same seed produces identical results.
    demo.run(tmp_path / "out2", tmp_path / "data2", ROOT / "configs")
    assert (tmp_path / "out2" / "results.json").read_text() == (out / "results.json").read_text()


def test_airflow_example_gate_without_airflow(tmp_path):
    spec = importlib.util.spec_from_file_location("airflow_dag", ROOT / "examples" / "airflow_dag.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # must import cleanly with Airflow absent
    demo.generate(tmp_path)
    with pytest.raises(CircuitOpenError, match="downstream load blocked"):
        module.reliability_gate(tmp_path, as_of=demo.AS_OF)
