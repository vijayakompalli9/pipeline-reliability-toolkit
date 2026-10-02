"""Pipeline Reliability Toolkit.

Config-driven data quality checks, schema-drift detection, source-to-target
reconciliation, a circuit breaker for downstream loads, and an incident
summarizer that turns aggregated check results into a root-cause note.
"""

from reliability.results import CheckResult, Severity

__all__ = ["CheckResult", "Severity"]
__version__ = "0.1.0"
