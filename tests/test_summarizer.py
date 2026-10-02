import sys

import pandas as pd
import pytest

from reliability.breaker import CircuitBreaker
from reliability.exceptions import SummarizerError
from reliability.lineage import Lineage
from reliability.pipeline import check_contract
from reliability.results import CheckResult, Severity
from reliability.summarizer import (
    AzureOpenAISummarizer,
    BedrockSummarizer,
    RuleBasedSummarizer,
    build_context,
    build_prompt,
    get_summarizer,
)

from .conftest import AS_OF, make_contract

LINEAGE = Lineage({
    "stg": {"downstream": ["mart_a"]},
    "mart_a": {"owner": "finance", "criticality": "high", "downstream": ["report_x", "stg"]},  # cycle
    "report_x": {"owner": "regulatory", "criticality": "critical"},
})


def recon_failures():
    return [
        CheckResult("rejected_accounting", "reconciliation", "stg", False, Severity.CRITICAL,
                    observed={"missing_keys": 12, "rejected_rows": 2, "unaccounted": 10}, message="10 unaccounted"),
        CheckResult("duplicates", "reconciliation", "stg", False, Severity.CRITICAL, column="id",
                    observed={"duplicate_keys": 4, "extra_rows": 4}, message="4 keys loaded more than once"),
        CheckResult("freshness", "quality", "stg", False, Severity.WARN, column="ts", message="newest is 30h old"),
        CheckResult("row_count_min", "quality", "stg", True, Severity.CRITICAL),
    ]


def test_lineage_downstream_handles_cycles():
    assert [(i.dataset, i.depth) for i in LINEAGE.downstream("stg")] == [("mart_a", 1), ("report_x", 2)]


def test_rule_based_note_sections_and_causes():
    results = recon_failures()
    ctx = build_context(results, CircuitBreaker().evaluate(results), "run-9", "2026-09-30", LINEAGE)
    note = RuleBasedSummarizer().summarize(ctx)
    for heading in ("## What failed", "## Likely cause", "## Blast radius", "## Suggested next steps"):
        assert heading in note
    assert "Failed-then-retried load" in note
    assert "Partial load" in note and "10 source rows" in note
    assert "Late delivery" in note
    assert "`report_x` | 2 | regulatory | critical" in note
    assert "MERGE" in note
    assert "OPEN" in note


def test_clean_run_note():
    results = [CheckResult("unique", "quality", "stg", True, Severity.CRITICAL)]
    ctx = build_context(results, CircuitBreaker().evaluate(results), "r", "t")
    note = RuleBasedSummarizer().summarize(ctx)
    assert "All checks passed" in note and "CLOSED" in note


def test_prompt_contains_only_aggregates_never_raw_rows():
    secret = "SSN-123-45-6789"
    df = pd.DataFrame({"order_id": ["O-1", "O-1"], "customer_id": [secret, "C-2"], "qty": [1, 2],
                       "status": ["OPEN", "BAD"], "created_at": pd.to_datetime(["2026-09-30", "2026-09-30"])})
    contract = make_contract([
        {"type": "unique", "column": "order_id"},
        {"type": "regex", "column": "customer_id", "pattern": r"^C-\d$"},
        {"type": "accepted_values", "column": "status", "values": ["OPEN"]},
        {"type": "referential", "column": "customer_id", "ref_table": "nope", "ref_column": "id"},
    ])
    results = check_contract(contract, {"orders": df}, AS_OF)
    assert sum(not r.passed for r in results) == 4
    ctx = build_context(results, CircuitBreaker().evaluate(results), "r", "t")
    prompt = build_prompt(ctx)
    assert secret not in prompt and "BAD" not in prompt and "C-2" not in prompt
    assert "check could not be evaluated" in prompt  # engine error text is dropped
    assert secret not in RuleBasedSummarizer().summarize(ctx)


def test_llm_adapters_are_lazy(monkeypatch):
    monkeypatch.setitem(sys.modules, "boto3", None)  # importing would fail
    monkeypatch.delenv("AWS_REGION", raising=False)
    bedrock = BedrockSummarizer()  # construction needs no SDK, creds, or network
    with pytest.raises(SummarizerError, match="AWS_REGION"):
        _ = bedrock.client
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "placeholder")
    with pytest.raises(SummarizerError, match="boto3"):
        _ = bedrock.client
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    with pytest.raises(SummarizerError, match="AZURE_OPENAI_ENDPOINT"):
        _ = AzureOpenAISummarizer().client


def test_get_summarizer(monkeypatch):
    monkeypatch.delenv("RELIABILITY_SUMMARIZER", raising=False)
    assert isinstance(get_summarizer(), RuleBasedSummarizer)
    assert isinstance(get_summarizer("bedrock"), BedrockSummarizer)
    with pytest.raises(SummarizerError):
        get_summarizer("gpt-local")
