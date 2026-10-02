import pytest

from reliability.contracts import RuleSpec
from reliability.results import Severity
from reliability.rules import run_contract_rules, run_rule

from .conftest import AS_OF, make_contract


def rule(rtype, **params):
    return RuleSpec(rtype, Severity.CRITICAL, params)


@pytest.mark.parametrize(
    "spec, passed, failed_count",
    [
        (rule("not_null", column="order_id"), True, 0),
        (rule("unique", column="order_id"), True, 0),
        (rule("unique", column="customer_id"), False, 1),
        (rule("unique", columns=["customer_id", "status"]), True, 0),  # composite key is unique
        (rule("range", column="qty", min=1, max=10), True, 0),
        (rule("range", column="qty", max=4), False, 2),
        (rule("regex", column="order_id", pattern=r"^O-\d+$"), True, 0),
        (rule("regex", column="customer_id", pattern=r"^C-[12]$"), False, 1),
        (rule("accepted_values", column="status", values=["OPEN", "SHIPPED", "CLOSED"]), True, 0),
        (rule("accepted_values", column="status", values=["OPEN"]), False, 2),
        (rule("freshness", column="created_at", max_age_hours=2), True, 0),
        (rule("freshness", column="created_at", max_age_hours=0.5), False, 1),
        (rule("row_count_min", min=4), True, 0),
        (rule("row_count_min", min=5), False, 1),
        (rule("referential", column="customer_id", ref_table="customers", ref_column="customer_id"), True, 0),
    ],
)
def test_rule_types(loaded, spec, passed, failed_count):
    result = run_rule(loaded, "orders", spec, AS_OF)
    assert result.passed is passed, result.message
    assert result.failed_count == failed_count


def test_not_null_detects_nulls_and_threshold(loaded):
    loaded.execute("UPDATE orders SET customer_id = NULL WHERE order_id = 'O-4'")
    strict = run_rule(loaded, "orders", rule("not_null", column="customer_id"), AS_OF)
    lenient = run_rule(loaded, "orders", rule("not_null", column="customer_id", max_null_pct=30), AS_OF)
    assert not strict.passed and strict.observed == {"null_rows": 1, "null_pct": 25.0}
    assert lenient.passed


def test_referential_orphans(loaded):
    loaded.execute("DELETE FROM customers WHERE customer_id = 'C-2'")
    r = run_rule(loaded, "orders", rule("referential", column="customer_id", ref_table="customers",
                                        ref_column="customer_id"), AS_OF)
    assert not r.passed and r.observed == {"orphan_rows": 2, "orphan_keys": 1}


def test_sql_error_becomes_failed_result(loaded):
    r = run_rule(loaded, "orders", rule("referential", column="customer_id", ref_table="missing_tbl",
                                        ref_column="id"), AS_OF)
    assert r.error and not r.passed and "could not be evaluated" in r.message


def test_run_contract_rules_includes_implicit(loaded):
    results = run_contract_rules(loaded, make_contract([{"type": "row_count_min", "min": 1}]), AS_OF)
    assert [r.check for r in results] == ["not_null", "row_count_min"]
    assert all(r.passed for r in results)
