import pytest

from reliability.drift import detect_drift, drift_results, type_family
from reliability.results import Severity

from .conftest import make_contract

BASE = {"order_id": "VARCHAR", "customer_id": "VARCHAR", "qty": "BIGINT", "status": "VARCHAR",
        "created_at": "TIMESTAMP"}


@pytest.mark.parametrize("t, fam", [("BIGINT", "integer"), ("DECIMAL(18,2)", "number"), ("varchar", "string"),
                                    ("TIMESTAMP WITH TIME ZONE", "timestamp"), ("TIMESTAMP_NS", "timestamp"),
                                    ("number", "number"), ("UUID", "uuid")])
def test_type_family(t, fam):
    assert type_family(t) == fam


def test_no_drift_within_family():
    changes = detect_drift(make_contract([]), BASE)
    assert changes == []
    [r] = drift_results("orders", changes)
    assert r.passed


@pytest.mark.parametrize(
    "observed_qty, breaking",
    [("DOUBLE", False), ("VARCHAR", True), ("BOOLEAN", True)],
)
def test_type_change_classification(observed_qty, breaking):
    [c] = detect_drift(make_contract([]), {**BASE, "qty": observed_qty})
    assert c.change == "type_changed" and c.breaking is breaking


def test_removed_is_breaking_added_is_not():
    observed = {k: v for k, v in BASE.items() if k != "status"} | {"channel": "VARCHAR"}
    changes = {c.change: c for c in detect_drift(make_contract([]), observed)}
    assert changes["removed"].breaking and changes["removed"].column == "status"
    assert not changes["added"].breaking
    sev = {r.check: r.severity for r in drift_results("orders", list(changes.values()))}
    assert sev == {"column_removed": Severity.CRITICAL, "column_added": Severity.WARN}


def test_strict_contract_makes_added_breaking():
    contract = make_contract([], drift={"allow_added_columns": False})
    [c] = detect_drift(contract, {**BASE, "channel": "VARCHAR"})
    assert c.change == "added" and c.breaking


def test_date_to_timestamp_is_widening():
    contract = make_contract([], schema=[{"name": "d", "type": "date"}])
    [c] = detect_drift(contract, {"d": "TIMESTAMP"})
    assert not c.breaking
