"""Incident context: the aggregated, row-free view of a run that summarizers consume.

Security design: this is the ONLY object passed to any summarizer, including the
optional LLM adapters. It is built from check *metadata* (check names, columns,
counts, percentages, sums, types, timestamps of the newest record) and lineage.
It never contains row values. Engine error messages are dropped because database
errors can echo offending values (e.g. "could not convert 'X'").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from reliability.breaker import BreakerDecision
from reliability.lineage import Impact, Lineage
from reliability.results import CheckResult

_PROMPT_FIELDS = ("check", "category", "dataset", "column", "severity", "observed", "expected", "failed_count")


@dataclass
class IncidentContext:
    run_id: str
    as_of: str
    datasets: list[str]
    state: str
    blocking: list[CheckResult]
    warnings: list[CheckResult]
    passed_count: int
    blast_radius: list[Impact] = field(default_factory=list)

    def to_prompt_dict(self) -> dict[str, Any]:
        """Aggregated metadata safe to send to an external model."""

        def safe(r: CheckResult) -> dict[str, Any]:
            item = {k: getattr(r, k) for k in _PROMPT_FIELDS}
            item["severity"] = r.severity.value
            item["summary"] = "check could not be evaluated" if r.error else r.message
            return item

        return {
            "run_id": self.run_id,
            "as_of": self.as_of,
            "datasets": self.datasets,
            "circuit_state": self.state,
            "blocking_failures": [safe(r) for r in self.blocking],
            "warnings": [safe(r) for r in self.warnings],
            "passed_checks": self.passed_count,
            "downstream_impact": [
                {"dataset": i.dataset, "depth": i.depth, "owner": i.owner, "criticality": i.criticality}
                for i in self.blast_radius
            ],
        }


def build_context(
    results: list[CheckResult],
    decision: BreakerDecision,
    run_id: str,
    as_of: str,
    lineage: Lineage | None = None,
) -> IncidentContext:
    failed_datasets = sorted({r.dataset for r in decision.blocking + decision.warnings})
    impacts: dict[str, Impact] = {}
    if lineage:
        for ds in failed_datasets:
            for impact in lineage.downstream(ds):
                if impact.dataset not in failed_datasets and (
                    impact.dataset not in impacts or impact.depth < impacts[impact.dataset].depth
                ):
                    impacts[impact.dataset] = impact
    return IncidentContext(
        run_id=run_id,
        as_of=as_of,
        datasets=sorted({r.dataset for r in results}),
        state=decision.state.value,
        blocking=decision.blocking,
        warnings=decision.warnings,
        passed_count=sum(r.passed for r in results),
        blast_radius=sorted(impacts.values(), key=lambda i: (i.depth, i.dataset)),
    )
