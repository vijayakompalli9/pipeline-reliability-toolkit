"""Data quality rule engine. Every rule is a single aggregate SQL query in DuckDB."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import duckdb

from reliability.contracts import Contract, RuleSpec
from reliability.io import quote_ident as q
from reliability.logging_utils import log_event
from reliability.results import CheckResult

logger = logging.getLogger(__name__)

# A rule returns (failed_count, observed, expected, message).
Outcome = tuple[int, Any, Any, str]


def _scalar(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> Any:
    return con.execute(sql, params or []).fetchone()[0]


def _not_null(con, table: str, rule: RuleSpec, _as_of: datetime) -> Outcome:
    col = q(rule.column)
    nulls, total = con.execute(f"SELECT count(*) FILTER (WHERE {col} IS NULL), count(*) FROM {table}").fetchone()
    pct = round(100.0 * nulls / total, 3) if total else 0.0
    allowed = float(rule.params.get("max_null_pct", 0.0))
    failed = nulls if pct > allowed else 0
    return failed, {"null_rows": nulls, "null_pct": pct}, {"max_null_pct": allowed}, f"{nulls} null rows ({pct}%)"


def _unique(con, table: str, rule: RuleSpec, _as_of: datetime) -> Outcome:
    cols = rule.params.get("columns") or [rule.column]
    keys = ", ".join(q(c) for c in cols)
    groups, extra = con.execute(
        f"SELECT count(*), coalesce(sum(n - 1), 0) FROM "
        f"(SELECT count(*) AS n FROM {table} GROUP BY {keys} HAVING count(*) > 1)"
    ).fetchone()
    return int(extra), {"duplicate_keys": groups, "extra_rows": int(extra)}, "0 duplicates", (
        f"{groups} keys duplicated, {int(extra)} extra rows"
    )


def _range(con, table: str, rule: RuleSpec, _as_of: datetime) -> Outcome:
    col = f"TRY_CAST({q(rule.column)} AS DOUBLE)"
    lo, hi = rule.params.get("min"), rule.params.get("max")
    conds = []
    if lo is not None:
        conds.append(f"{col} < {float(lo)}")
    if hi is not None:
        conds.append(f"{col} > {float(hi)}")
    bad, mn, mx = con.execute(
        f"SELECT count(*) FILTER (WHERE {' OR '.join(conds)}), min({col}), max({col}) FROM {table}"
    ).fetchone()
    return bad, {"min": mn, "max": mx}, {"min": lo, "max": hi}, f"{bad} rows outside [{lo}, {hi}]"


def _regex(con, table: str, rule: RuleSpec, _as_of: datetime) -> Outcome:
    col = f"CAST({q(rule.column)} AS VARCHAR)"
    bad = _scalar(
        con,
        f"SELECT count(*) FROM {table} WHERE {col} IS NOT NULL AND NOT regexp_full_match({col}, ?)",
        [rule.params["pattern"]],
    )
    return bad, {"non_matching_rows": bad}, rule.params["pattern"], f"{bad} rows do not match pattern"


def _accepted_values(con, table: str, rule: RuleSpec, _as_of: datetime) -> Outcome:
    col = f"CAST({q(rule.column)} AS VARCHAR)"
    allowed = [str(v) for v in rule.params["values"]]
    bad, distinct_bad = con.execute(
        f"SELECT count(*), count(DISTINCT {col}) FROM {table} "
        f"WHERE {col} IS NOT NULL AND NOT list_contains(?, {col})",
        [allowed],
    ).fetchone()
    return bad, {"rows": bad, "distinct_unexpected_values": distinct_bad}, allowed, (
        f"{bad} rows with {distinct_bad} unexpected distinct values"
    )


def _freshness(con, table: str, rule: RuleSpec, as_of: datetime) -> Outcome:
    latest = _scalar(con, f"SELECT max(CAST({q(rule.column)} AS TIMESTAMP)) FROM {table}")
    limit = float(rule.params["max_age_hours"])
    if latest is None:
        return 1, {"latest": None}, {"max_age_hours": limit}, "no timestamps present"
    ref = as_of.astimezone(UTC).replace(tzinfo=None) if as_of.tzinfo else as_of  # data timestamps are UTC
    age = (ref - latest).total_seconds() / 3600
    return int(age > limit), {"latest": latest.isoformat(), "age_hours": round(age, 2)}, {"max_age_hours": limit}, (
        f"newest record is {age:.1f}h old (limit {limit:g}h)"
    )


def _row_count_min(con, table: str, rule: RuleSpec, _as_of: datetime) -> Outcome:
    n = _scalar(con, f"SELECT count(*) FROM {table}")
    minimum = int(rule.params["min"])
    return int(n < minimum), n, {"min": minimum}, f"{n} rows (minimum {minimum})"


def _referential(con, table: str, rule: RuleSpec, _as_of: datetime) -> Outcome:
    col, ref_col = q(rule.column), q(rule.params["ref_column"])
    ref_table = q(rule.params["ref_table"])
    orphans, distinct = con.execute(
        f"SELECT count(*), count(DISTINCT t.{col}) FROM {table} t "
        f"WHERE t.{col} IS NOT NULL AND NOT EXISTS "
        f"(SELECT 1 FROM {ref_table} r WHERE r.{ref_col} = t.{col})"
    ).fetchone()
    target = f"{rule.params['ref_table']}.{rule.params['ref_column']}"
    return orphans, {"orphan_rows": orphans, "orphan_keys": distinct}, target, (
        f"{orphans} rows reference {distinct} keys missing from {target}"
    )


RULES: dict[str, Callable[..., Outcome]] = {
    "not_null": _not_null,
    "unique": _unique,
    "range": _range,
    "regex": _regex,
    "accepted_values": _accepted_values,
    "freshness": _freshness,
    "row_count_min": _row_count_min,
    "referential": _referential,
}


def run_rule(con: duckdb.DuckDBPyConnection, dataset: str, rule: RuleSpec, as_of: datetime) -> CheckResult:
    """Evaluate one rule. SQL errors become failed results instead of crashing the run."""
    base = {"check": rule.type, "category": "quality", "dataset": dataset,
            "severity": rule.severity, "column": rule.column}
    try:
        failed, observed, expected, message = RULES[rule.type](con, q(dataset), rule, as_of)
    except duckdb.Error as exc:
        log_event(logger, "rule_error", logging.WARNING, rule=rule.name, error=type(exc).__name__)
        message = f"rule could not be evaluated: {exc}".splitlines()[0]
        return CheckResult(**base, passed=False, error=True, message=message)
    details = {"source": rule.params["source"]} if "source" in rule.params else {}
    return CheckResult(
        **base, passed=failed == 0, observed=observed, expected=expected,
        failed_count=int(failed), message=message, details=details,
    )


def run_contract_rules(
    con: duckdb.DuckDBPyConnection, contract: Contract, as_of: datetime | None = None
) -> list[CheckResult]:
    """Run every effective rule in a contract against table ``contract.dataset``."""
    as_of = as_of or datetime.now(UTC)
    results = [run_rule(con, contract.dataset, rule, as_of) for rule in contract.effective_rules()]
    log_event(
        logger, "quality_checks_complete", dataset=contract.dataset,
        total=len(results), failed=sum(not r.passed for r in results),
    )
    return results
