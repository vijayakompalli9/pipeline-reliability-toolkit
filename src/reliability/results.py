"""Check result model shared by quality rules, drift detection, and reconciliation.

Results carry *aggregated* metadata only (counts, percentages, totals, types).
They never hold raw row values, which is what lets them be safely forwarded to
alerting channels and optional LLM summarizers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    WARN = "warn"
    CRITICAL = "critical"


@dataclass
class CheckResult:
    check: str
    category: str  # "quality" | "drift" | "reconciliation"
    dataset: str
    passed: bool
    severity: Severity
    column: str | None = None
    observed: Any = None
    expected: Any = None
    failed_count: int = 0
    message: str = ""
    error: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_critical_failure(self) -> bool:
        return not self.passed and self.severity == Severity.CRITICAL

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["severity"] = self.severity.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckResult:
        payload = dict(data)
        payload["severity"] = Severity(payload["severity"])
        return cls(**payload)


def failures(results: list[CheckResult], severity: Severity | None = None) -> list[CheckResult]:
    """Return failed results, optionally filtered by severity."""
    return [r for r in results if not r.passed and (severity is None or r.severity == severity)]
