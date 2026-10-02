import pytest

from reliability.contracts import load_contract, parse_contract
from reliability.exceptions import ContractError
from reliability.results import Severity

from .conftest import make_contract


def test_repo_contract_loads():
    c = load_contract("configs/contracts/payments_source.yaml")
    assert c.dataset == "payments_source"
    assert {r.type for r in c.rules} >= {"unique", "regex", "range", "freshness", "referential"}


def test_implicit_not_null_from_schema():
    c = make_contract([])
    rules = c.effective_rules()
    assert [(r.type, r.column, r.severity) for r in rules] == [("not_null", "order_id", Severity.CRITICAL)]


@pytest.mark.parametrize(
    "rule, fragment",
    [
        ({"type": "bogus", "column": "x"}, "unknown type"),
        ({"type": "regex", "column": "x"}, "pattern"),
        ({"type": "range", "column": "x"}, "min|max"),
        ({"type": "unique"}, "column|columns"),
        ({"type": "not_null", "column": "x", "severity": "fatal"}, "severity"),
    ],
)
def test_invalid_rules_rejected(rule, fragment):
    with pytest.raises(ContractError, match=fragment.replace("|", r"\|")):
        make_contract([rule])


def test_missing_dataset_and_file():
    with pytest.raises(ContractError, match="dataset"):
        parse_contract({"schema": [{"name": "a", "type": "string"}]})
    with pytest.raises(ContractError, match="not found"):
        load_contract("nope.yaml")
