"""Markdown run report."""

from __future__ import annotations

from reliability.breaker import BreakerDecision
from reliability.results import CheckResult

_TITLES = {"drift": "Schema drift", "quality": "Data quality", "reconciliation": "Reconciliation"}


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|")


def render_report(run_id: str, as_of: str, results: list[CheckResult], decision: BreakerDecision) -> str:
    passed = sum(r.passed for r in results)
    lines = [
        "# Data reliability report",
        "",
        f"- **Run:** `{run_id}`  ",
        f"- **As of:** {as_of}  ",
        f"- **Circuit breaker:** **{decision.state.value.upper()}** (exit code {decision.exit_code}) - "
        f"{decision.reason()}  ",
        f"- **Checks:** {len(results)} total, {passed} passed, {len(decision.blocking)} blocking, "
        f"{len(decision.warnings)} warnings",
    ]
    for category, title in _TITLES.items():
        group = [r for r in results if r.category == category]
        if not group:
            continue
        lines += ["", f"## {title}", "", "| Status | Severity | Dataset | Check | Column | Detail |",
                  "|---|---|---|---|---|---|"]
        for r in sorted(group, key=lambda r: (r.passed, r.severity.value != "critical")):
            status = "PASS" if r.passed else ("ERROR" if r.error else "FAIL")
            lines.append(f"| {status} | {r.severity.value} | {r.dataset} | {r.check} | {r.column or ''} | "
                         f"{_cell(r.message)} |")
    return "\n".join(lines) + "\n"
