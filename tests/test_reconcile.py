import pandas as pd
import pytest

from reliability.io import register
from reliability.reconcile import parse_recon_config, reconcile, within_tolerance


@pytest.mark.parametrize(
    "src, tgt, tol, ok",
    [
        (1000, 995, {"tolerance_pct": 0.5}, True),
        (1000, 994, {"tolerance_pct": 0.5}, False),
        (1000, 998, {"tolerance_abs": 2}, True),
        (100.0, 100.0, {}, True),
        (100.0, 100.01, {}, False),
        (10.0, 12.0, {"tolerance_abs": 3, "tolerance_pct": 1}, True),  # larger of the two wins
    ],
)
def test_within_tolerance(src, tgt, tol, ok):
    assert within_tolerance(src, tgt, tol)[0] is ok


def _setup(con, target_ids, rejected_ids=(), amounts=None):
    src = pd.DataFrame({"id": list(range(1, 101)), "amount": [10.0] * 100, "ref": ["x"] * 100})
    tgt = src.set_index("id").loc[list(target_ids)].reset_index()
    if amounts is not None:
        tgt["amount"] = amounts
    register(con, "src", src)
    register(con, "tgt", tgt)
    register(con, "rej", pd.DataFrame({"id": list(rejected_ids)}, dtype="int64"))


def _cfg(**overrides):
    base = {
        "name": "t", "source": "src", "target": "tgt", "rejected": "rej", "key": "id",
        "row_count": {"tolerance_pct": 1.0},
        "control_totals": [{"column": "amount", "tolerance_abs": 0.01}],
        "duplicates": {}, "null_thresholds": [{"column": "ref", "max_null_pct": 0}],
        "rejected_records": {"tolerance_rows": 0},
    }
    return parse_recon_config({**base, **overrides})


def by_check(results):
    return {r.check: r for r in results}


def test_clean_load_passes(con):
    _setup(con, range(1, 101))
    assert all(r.passed for r in reconcile(con, _cfg()))


def test_row_count_tolerance(con):
    _setup(con, range(2, 101), rejected_ids=[1])  # 1% short, explained by a reject
    res = by_check(reconcile(con, _cfg()))
    assert res["row_count"].passed
    assert res["rejected_accounting"].passed
    assert not res["control_total"].passed  # 10.00 short, abs tolerance 0.01
    res = by_check(reconcile(con, _cfg(row_count={"tolerance_pct": 0.5})))
    assert not res["row_count"].passed and res["row_count"].observed["diff"] == 1


def test_duplicates_and_unaccounted_missing(con):
    ids = [*range(1, 96), 5, 6, 7]  # 5 missing, 3 duplicated
    _setup(con, ids, rejected_ids=[96, 97])
    res = by_check(reconcile(con, _cfg()))
    assert res["duplicates"].observed == {"duplicate_keys": 3, "extra_rows": 3}
    assert res["rejected_accounting"].observed == {"missing_keys": 5, "rejected_rows": 2, "unaccounted": 3}
    assert not res["rejected_accounting"].passed
    lenient = by_check(reconcile(con, _cfg(rejected_records={"tolerance_rows": 3}, duplicates={"tolerance_rows": 3})))
    assert lenient["rejected_accounting"].passed and lenient["duplicates"].passed


def test_null_threshold(con):
    _setup(con, range(1, 101))
    con.execute("UPDATE tgt SET ref = NULL WHERE id <= 2")
    res = by_check(reconcile(con, _cfg()))
    assert not res["null_threshold"].passed and res["null_threshold"].observed["null_pct"] == 2.0
    res = by_check(reconcile(con, _cfg(null_thresholds=[{"column": "ref", "max_null_pct": 5, "severity": "warn"}])))
    assert res["null_threshold"].passed


def test_control_total_pct_tolerance(con):
    _setup(con, range(1, 101), amounts=[10.0] * 99 + [10.5])  # +0.05% of 1000
    res = by_check(reconcile(con, _cfg(control_totals=[{"column": "amount", "tolerance_pct": 0.1}])))
    assert res["control_total"].passed
