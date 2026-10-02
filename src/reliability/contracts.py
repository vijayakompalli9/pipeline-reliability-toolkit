"""YAML data contracts: expected schema plus quality rules per dataset."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from reliability.exceptions import ContractError
from reliability.results import Severity

# Rule type -> parameters that must be present.
RULE_PARAMS: dict[str, tuple[str, ...]] = {
    "not_null": ("column",),
    "unique": (),  # "column" or "columns"
    "range": ("column",),  # "min" and/or "max"
    "regex": ("column", "pattern"),
    "accepted_values": ("column", "values"),
    "freshness": ("column", "max_age_hours"),
    "row_count_min": ("min",),
    "referential": ("column", "ref_table", "ref_column"),
}


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    type: str
    nullable: bool = True


@dataclass(frozen=True)
class RuleSpec:
    type: str
    severity: Severity
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def column(self) -> str | None:
        return self.params.get("column")

    @property
    def name(self) -> str:
        cols = self.params.get("columns") or ([self.column] if self.column else [])
        return f"{self.type}:{','.join(cols)}" if cols else self.type


@dataclass
class Contract:
    dataset: str
    columns: list[ColumnSpec]
    rules: list[RuleSpec]
    owner: str = "unknown"
    description: str = ""
    allow_added_columns: bool = True

    def effective_rules(self) -> list[RuleSpec]:
        """Explicit rules plus an implicit critical not_null for every non-nullable column."""
        explicit = {(r.type, r.column) for r in self.rules}
        implicit = [
            RuleSpec("not_null", Severity.CRITICAL, {"column": c.name, "source": "schema"})
            for c in self.columns
            if not c.nullable and ("not_null", c.name) not in explicit
        ]
        return implicit + list(self.rules)


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Read a YAML mapping, raising ContractError with a clear message on failure."""
    p = Path(path)
    if not p.is_file():
        raise ContractError(f"config file not found: {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ContractError(f"invalid YAML in {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise ContractError(f"{p} must contain a YAML mapping at the top level")
    return data


def parse_contract(data: dict[str, Any], origin: str = "<dict>") -> Contract:
    """Validate a contract mapping and build a :class:`Contract`."""
    dataset = data.get("dataset")
    if not dataset or not isinstance(dataset, str):
        raise ContractError(f"{origin}: 'dataset' is required")
    columns = []
    for i, col in enumerate(data.get("schema") or []):
        if "name" not in col or "type" not in col:
            raise ContractError(f"{origin}: schema[{i}] needs 'name' and 'type'")
        columns.append(ColumnSpec(col["name"], str(col["type"]), bool(col.get("nullable", True))))
    if not columns:
        raise ContractError(f"{origin}: 'schema' must list at least one column")

    rules = []
    for i, raw in enumerate(data.get("rules") or []):
        params = dict(raw)
        rtype = params.pop("type", None)
        if rtype not in RULE_PARAMS:
            raise ContractError(f"{origin}: rules[{i}] has unknown type {rtype!r}")
        missing = [p for p in RULE_PARAMS[rtype] if p not in params]
        if rtype == "unique" and not ({"column", "columns"} & params.keys()):
            missing.append("column|columns")
        if rtype == "range" and not ({"min", "max"} & params.keys()):
            missing.append("min|max")
        if missing:
            raise ContractError(f"{origin}: rules[{i}] ({rtype}) missing {missing}")
        try:
            severity = Severity(params.pop("severity", "critical"))
        except ValueError as exc:
            raise ContractError(f"{origin}: rules[{i}] severity must be warn|critical") from exc
        rules.append(RuleSpec(rtype, severity, params))

    drift_cfg = data.get("drift") or {}
    return Contract(
        dataset=dataset,
        columns=columns,
        rules=rules,
        owner=data.get("owner", "unknown"),
        description=data.get("description", ""),
        allow_added_columns=bool(drift_cfg.get("allow_added_columns", True)),
    )


def load_contract(path: str | Path) -> Contract:
    return parse_contract(load_yaml(path), origin=str(path))
