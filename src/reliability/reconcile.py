"""Source-to-target reconciliation with configurable tolerances.

Checks: row counts, control totals (column sums), duplicate keys in the target,
null-percentage thresholds, and rejected-record accounting
(``source keys == loaded keys + rejected rows``, within tolerance).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from reliability.contracts import load_yaml
from reliability.exceptions import ContractError
from reliability.io import quote_ident as q
from reliability.logging_utils import log_event
from reliability.results import CheckResult, Severity

logger = logging.getLogger(__name__)


@dataclass
class ReconConfig:
    name: str
    source: str
    target: str
    key: list[str]
    rejected: str | None = None
    row_count: dict[str, Any] = field(default_factory=dict)
    control_totals: list[dict[str, Any]] = field(default_factory=list)
    duplicates: dict[str, Any] | None = None
    null_thresholds: list[dict[str, Any]] = field(default_factory=list)
    rejected_records: dict[str, Any] | None = None


def parse_recon_config(data: dict[str, Any], origin: str = "<dict>") -> ReconConfig:
    for required in ("name", "source", "target", "key"):
        if required not in data:
            raise ContractError(f"{origin}: reconciliation config needs '{required}'")
    key = data["key"] if isinstance(data["key"], list) else [data["key"]]
    return ReconConfig(
        name=data["name"], source=data["source"], target=data["target"], key=key,
        rejected=data.get("rejected"),
        row_count=data.get("row_count") or {},
        control_totals=data.get("control_totals") or [],
        duplicates=data.get("duplicates"),
        null_thresholds=data.get("null_thresholds") or [],
        rejected_records=data.get("rejected_records"),
    )


def load_recon_config(path: str | Path) -> ReconConfig:
    return parse_recon_config(load_yaml(path), origin=str(path))


def within_tolerance(source: float, target: float, tol: dict[str, Any]) -> tuple[bool, float]:
    """Pass if |source - target| <= max(tolerance_abs, tolerance_pct% of |source|)."""
    allowed = max(float(tol.get("tolerance_abs", 0)), abs(source) * float(tol.get("tolerance_pct", 0)) / 100)
    diff = abs(source - target)
    return diff <= allowed + 1e-9, round(allowed, 6)


def _sev(cfg: dict[str, Any] | None) -> Severity:
    return Severity((cfg or {}).get("severity", "critical"))


def reconcile(con: duckdb.DuckDBPyConnection, cfg: ReconConfig) -> list[CheckResult]:
    """Run all configured reconciliation checks and return their results."""
    src, tgt = q(cfg.source), q(cfg.target)
    keys = ", ".join(q(k) for k in cfg.key)
    ds = cfg.target
    results: list[CheckResult] = []

    def count(table: str) -> int:
        return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    s_rows, t_rows = count(src), count(tgt)
    ok, allowed = within_tolerance(s_rows, t_rows, cfg.row_count)
    diff = s_rows - t_rows
    pct = round(100 * diff / s_rows, 3) if s_rows else 0.0
    results.append(CheckResult(
        "row_count", "reconciliation", ds, ok, _sev(cfg.row_count),
        observed={"source": s_rows, "target": t_rows, "diff": diff, "diff_pct": pct},
        expected={"max_abs_diff": allowed}, failed_count=0 if ok else abs(diff),
        message=f"source={s_rows} target={t_rows} diff={diff} ({pct}%)",
    ))

    for ct in cfg.control_totals:
        col = q(ct["column"])
        s_sum = float(con.execute(f"SELECT coalesce(sum({col}), 0) FROM {src}").fetchone()[0])
        t_sum = float(con.execute(f"SELECT coalesce(sum({col}), 0) FROM {tgt}").fetchone()[0])
        ok, allowed = within_tolerance(s_sum, t_sum, ct)
        results.append(CheckResult(
            "control_total", "reconciliation", ds, ok, _sev(ct), column=ct["column"],
            observed={"source_sum": round(s_sum, 2), "target_sum": round(t_sum, 2), "diff": round(s_sum - t_sum, 2)},
            expected={"max_abs_diff": allowed}, failed_count=0 if ok else 1,
            message=f"sum({ct['column']}) source={s_sum:,.2f} target={t_sum:,.2f} diff={s_sum - t_sum:,.2f}",
        ))

    if cfg.duplicates is not None:
        groups, extra = con.execute(
            f"SELECT count(*), coalesce(sum(n - 1), 0) FROM "
            f"(SELECT count(*) n FROM {tgt} GROUP BY {keys} HAVING count(*) > 1)"
        ).fetchone()
        extra = int(extra)
        ok = extra <= int(cfg.duplicates.get("tolerance_rows", 0))
        results.append(CheckResult(
            "duplicates", "reconciliation", ds, ok, _sev(cfg.duplicates), column=",".join(cfg.key),
            observed={"duplicate_keys": groups, "extra_rows": extra},
            expected={"max_extra_rows": cfg.duplicates.get("tolerance_rows", 0)},
            failed_count=extra, message=f"{groups} keys loaded more than once ({extra} extra rows)",
        ))

    for nt in cfg.null_thresholds:
        col = q(nt["column"])
        nulls = con.execute(f"SELECT count(*) FILTER (WHERE {col} IS NULL) FROM {tgt}").fetchone()[0]
        null_pct = round(100 * nulls / t_rows, 3) if t_rows else 0.0
        ok = null_pct <= float(nt["max_null_pct"])
        results.append(CheckResult(
            "null_threshold", "reconciliation", ds, ok, _sev(nt), column=nt["column"],
            observed={"null_rows": nulls, "null_pct": null_pct}, expected={"max_null_pct": nt["max_null_pct"]},
            failed_count=0 if ok else nulls, message=f"{null_pct}% null in target (limit {nt['max_null_pct']}%)",
        ))

    if cfg.rejected_records is not None:
        results.append(_rejected_accounting(con, cfg, src, tgt, keys))

    log_event(logger, "reconciliation_complete", name=cfg.name, total=len(results),
              failed=sum(not r.passed for r in results))
    return results


def _rejected_accounting(con, cfg: ReconConfig, src: str, tgt: str, keys: str) -> CheckResult:
    join = " AND ".join(f"s.{q(k)} = t.{q(k)}" for k in cfg.key)
    missing = con.execute(
        f"SELECT count(*) FROM (SELECT DISTINCT {keys} FROM {src}) s "
        f"WHERE NOT EXISTS (SELECT 1 FROM {tgt} t WHERE {join})"
    ).fetchone()[0]
    rejected = con.execute(f"SELECT count(*) FROM {q(cfg.rejected)}").fetchone()[0] if cfg.rejected else 0
    unaccounted = missing - rejected
    tol = int(cfg.rejected_records.get("tolerance_rows", 0))
    ok = abs(unaccounted) <= tol
    return CheckResult(
        "rejected_accounting", "reconciliation", cfg.target, ok, _sev(cfg.rejected_records),
        observed={"missing_keys": missing, "rejected_rows": rejected, "unaccounted": unaccounted},
        expected={"max_unaccounted": tol}, failed_count=0 if ok else abs(unaccounted),
        message=(f"{missing} source keys missing from target; {rejected} explained by rejects; "
                 f"{unaccounted} unaccounted"),
    )
