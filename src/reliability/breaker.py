"""Circuit breaker for downstream loads, plus webhook-shaped alert payloads.

* CLOSED: no blocking failures, downstream load may proceed (exit code 0).
* OPEN:   at least one failure at or above ``fail_on`` severity; the load must be
          blocked (exit code 2) and an alert payload is produced.

Webhook delivery is dry-run unless ``RELIABILITY_ALERT_DRY_RUN=false`` AND
``RELIABILITY_WEBHOOK_URL`` is set, so tests and demos never touch the network.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from reliability.exceptions import CircuitOpenError
from reliability.logging_utils import log_event
from reliability.results import CheckResult, Severity, failures

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_CIRCUIT_OPEN = 2
EXIT_TOOL_ERROR = 3


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"


@dataclass
class BreakerDecision:
    state: BreakerState
    exit_code: int
    blocking: list[CheckResult] = field(default_factory=list)
    warnings: list[CheckResult] = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.state == BreakerState.OPEN

    def reason(self) -> str:
        if not self.is_open:
            return f"all blocking checks passed ({len(self.warnings)} warnings)"
        names = ", ".join(f"{r.check}{':' + r.column if r.column else ''}" for r in self.blocking[:5])
        return f"{len(self.blocking)} blocking failure(s): {names}"


class CircuitBreaker:
    """Decide whether a downstream load may run, based on check results."""

    def __init__(self, fail_on: Severity = Severity.CRITICAL) -> None:
        self.fail_on = fail_on

    def evaluate(self, results: list[CheckResult]) -> BreakerDecision:
        failed = failures(results)
        if self.fail_on == Severity.WARN:
            blocking, warnings = failed, []
        else:
            blocking = [r for r in failed if r.severity == Severity.CRITICAL]
            warnings = [r for r in failed if r.severity == Severity.WARN]
        state = BreakerState.OPEN if blocking else BreakerState.CLOSED
        decision = BreakerDecision(state, EXIT_CIRCUIT_OPEN if blocking else EXIT_OK, blocking, warnings)
        log_event(logger, "breaker_evaluated", state=state.value, blocking=len(blocking), warnings=len(warnings))
        return decision

    def guard(self, results: list[CheckResult]) -> BreakerDecision:
        """Raise :class:`CircuitOpenError` if the circuit is open (for orchestrator tasks)."""
        decision = self.evaluate(results)
        if decision.is_open:
            raise CircuitOpenError(f"downstream load blocked: {decision.reason()}", decision)
        return decision


def build_alert(
    decision: BreakerDecision, dataset: str, run_id: str, fmt: str = "slack", note_url: str | None = None
) -> dict[str, Any]:
    """Build a Slack, Teams, or PagerDuty Events v2 shaped body (aggregated metadata only)."""
    title = f"[{decision.state.value.upper()}] Data reliability gate: {dataset}"
    lines = [f"- {r.severity.value}: {r.check}{' on ' + r.column if r.column else ''} - {r.message}"
             for r in decision.blocking + decision.warnings][:10]
    text = f"{decision.reason()}\n" + "\n".join(lines)
    if fmt == "slack":
        blocks: list[dict[str, Any]] = [
            {"type": "header", "text": {"type": "plain_text", "text": title}},
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"run_id `{run_id}`"}]},
        ]
        if note_url:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"<{note_url}|Incident note>"}})
        return {"text": title, "blocks": blocks}
    if fmt == "teams":
        facts = [{"title": "Run", "value": run_id}, {"title": "State", "value": decision.state.value}]
        return {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard", "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium"},
                        {"type": "TextBlock", "text": text, "wrap": True},
                        {"type": "FactSet", "facts": facts},
                    ],
                },
            }],
        }
    if fmt == "pagerduty":
        return {
            "routing_key": os.environ.get("PAGERDUTY_ROUTING_KEY", "<PAGERDUTY_ROUTING_KEY>"),
            "event_action": "trigger" if decision.is_open else "resolve",
            "dedup_key": "reliability-" + "-".join(dataset.replace(",", " ").split()),
            "payload": {
                "summary": f"{title}: {decision.reason()}"[:1024],
                "source": "pipeline-reliability-toolkit",
                "severity": "critical" if decision.is_open else "info",
                "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
                "custom_details": {
                    "run_id": run_id,
                    "blocking": [r.to_dict() for r in decision.blocking],
                    "warnings": [r.check for r in decision.warnings],
                },
            },
        }
    raise ValueError(f"unknown alert format {fmt!r}; use slack|teams|pagerduty")


def send_alert(payload: dict[str, Any], url: str | None = None, dry_run: bool | None = None) -> dict[str, Any]:
    """POST the payload to a webhook, or return a dry-run record (the default)."""
    url = url or os.environ.get("RELIABILITY_WEBHOOK_URL")
    if dry_run is None:
        dry_run = os.environ.get("RELIABILITY_ALERT_DRY_RUN", "true").lower() != "false"
    if dry_run or not url:
        log_event(logger, "alert_dry_run", configured=bool(url))
        return {"sent": False, "dry_run": True, "bytes": len(json.dumps(payload))}
    if not url.startswith("https://"):
        raise ValueError("webhook URL must use https://")
    req = urllib.request.Request(  # noqa: S310 - scheme validated above
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - URL comes from operator env
        status = resp.status
    log_event(logger, "alert_sent", status=status)
    return {"sent": True, "dry_run": False, "status": status}
