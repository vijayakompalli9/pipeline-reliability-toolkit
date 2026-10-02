"""Schema drift detection: compare an incoming table's schema with its contract.

Classification:
  * removed column                      -> breaking (downstream SELECTs fail)
  * added column                        -> non-breaking, unless the contract sets
                                           ``drift.allow_added_columns: false``
  * type change within a family         -> no drift (e.g. INTEGER -> BIGINT)
  * safe widening (integer -> number,
    date -> timestamp)                  -> non-breaking
  * any other type change               -> breaking (e.g. INTEGER -> VARCHAR)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from reliability.contracts import Contract
from reliability.results import CheckResult, Severity

_FAMILIES: dict[str, tuple[str, ...]] = {
    "integer": ("TINYINT", "SMALLINT", "INTEGER", "INT", "BIGINT", "HUGEINT",
                "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT"),
    "number": ("FLOAT", "REAL", "DOUBLE", "DECIMAL", "NUMERIC", "NUMBER"),
    "string": ("VARCHAR", "TEXT", "STRING", "CHAR"),
    "boolean": ("BOOLEAN", "BOOL"),
    "date": ("DATE",),
    "timestamp": ("TIMESTAMP", "DATETIME", "TIMESTAMPTZ"),
}
_WIDENING = {("integer", "number"), ("date", "timestamp")}


def type_family(type_name: str) -> str:
    """Map a DuckDB / contract type name to a logical family (unknown types map to themselves)."""
    t = type_name.strip().upper()
    if t.lower() in _FAMILIES:
        return t.lower()
    base = t.split("(")[0].split(" ")[0]
    if base.startswith("TIMESTAMP"):  # TIMESTAMP_NS, TIMESTAMP WITH TIME ZONE, ...
        return "timestamp"
    for family, names in _FAMILIES.items():
        if base in names:
            return family
    return t.lower()


@dataclass(frozen=True)
class DriftChange:
    column: str
    change: str  # "added" | "removed" | "type_changed"
    expected_type: str | None
    observed_type: str | None
    breaking: bool
    reason: str


def detect_drift(contract: Contract, observed: dict[str, str]) -> list[DriftChange]:
    """Return every schema difference between the contract and the observed schema."""
    obs = {k.lower(): (k, v) for k, v in observed.items()}
    expected = {c.name.lower(): c for c in contract.columns}
    changes: list[DriftChange] = []
    for key, col in expected.items():
        if key not in obs:
            changes.append(DriftChange(col.name, "removed", col.type, None, True, "column missing from incoming data"))
            continue
        exp_f, obs_f = type_family(col.type), type_family(obs[key][1])
        if exp_f == obs_f:
            continue
        widening = (exp_f, obs_f) in _WIDENING
        reason = f"safe widening {exp_f} -> {obs_f}" if widening else f"incompatible type {exp_f} -> {obs_f}"
        changes.append(DriftChange(col.name, "type_changed", col.type, obs[key][1], not widening, reason))
    for key, (name, typ) in obs.items():
        if key not in expected:
            strict = not contract.allow_added_columns
            reason = "new column not allowed by contract" if strict else "new column (additive)"
            changes.append(DriftChange(name, "added", None, typ, strict, reason))
    return changes


def drift_results(dataset: str, changes: list[DriftChange]) -> list[CheckResult]:
    """Convert drift changes to check results; an empty change list yields a single pass."""
    if not changes:
        return [CheckResult("schema_drift", "drift", dataset, True, Severity.CRITICAL,
                            message="schema matches contract")]
    return [
        CheckResult(
            check=f"column_{c.change}",
            category="drift",
            dataset=dataset,
            passed=False,
            severity=Severity.CRITICAL if c.breaking else Severity.WARN,
            column=c.column,
            observed=c.observed_type,
            expected=c.expected_type,
            failed_count=1,
            message=f"{'BREAKING' if c.breaking else 'non-breaking'}: {c.reason}",
            details=asdict(c),
        )
        for c in changes
    ]
