"""High-level API used by the CLI, the demo, and orchestrator integrations."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from reliability.breaker import BreakerDecision, build_alert, send_alert
from reliability.contracts import Contract
from reliability.drift import detect_drift, drift_results
from reliability.io import connect, observed_schema, register
from reliability.lineage import Lineage
from reliability.logging_utils import log_event
from reliability.reconcile import ReconConfig, reconcile
from reliability.report import render_report
from reliability.results import CheckResult
from reliability.rules import run_contract_rules
from reliability.summarizer import Summarizer, build_context, get_summarizer

logger = logging.getLogger(__name__)
Source = str | Path | pd.DataFrame


def _connect(con: duckdb.DuckDBPyConnection | None, data: dict[str, Source]) -> duckdb.DuckDBPyConnection:
    con = con or connect()
    for name, source in data.items():
        register(con, name, source)
    return con


def check_contract(
    contract: Contract,
    data: dict[str, Source],
    as_of: datetime | None = None,
    con: duckdb.DuckDBPyConnection | None = None,
) -> list[CheckResult]:
    """Schema drift + quality rules. ``data`` must include ``contract.dataset`` and any referenced tables."""
    con = _connect(con, data)
    changes = detect_drift(contract, observed_schema(con, contract.dataset))
    log_event(logger, "drift_detected", dataset=contract.dataset, changes=len(changes),
              breaking=sum(c.breaking for c in changes))
    return drift_results(contract.dataset, changes) + run_contract_rules(con, contract, as_of)


def reconcile_sources(
    cfg: ReconConfig, data: dict[str, Source], con: duckdb.DuckDBPyConnection | None = None
) -> list[CheckResult]:
    """Source-to-target reconciliation; ``data`` maps cfg.source/target/rejected names to files or frames."""
    return reconcile(_connect(con, data), cfg)


def write_outputs(
    out_dir: str | Path,
    run_id: str,
    results: list[CheckResult],
    decision: BreakerDecision,
    as_of: datetime | None = None,
    lineage: Lineage | None = None,
    alert_formats: tuple[str, ...] = ("slack",),
    summarizer: Summarizer | None = None,
) -> dict[str, Path]:
    """Write results.json, report.md, incident_note.md and alert_<fmt>.json; dry-run the webhook."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = (as_of or datetime.now(UTC)).isoformat(timespec="seconds")
    dataset = ", ".join(sorted({r.dataset for r in results}))
    paths: dict[str, Path] = {}

    payload: dict[str, Any] = {
        "run_id": run_id, "as_of": stamp, "circuit_state": decision.state.value,
        "exit_code": decision.exit_code, "results": [r.to_dict() for r in results],
    }
    paths["results"] = out / "results.json"
    paths["results"].write_text(json.dumps(payload, indent=2, default=str) + "\n")
    paths["report"] = out / "report.md"
    paths["report"].write_text(render_report(run_id, stamp, results, decision))

    ctx = build_context(results, decision, run_id, stamp, lineage)
    paths["incident_note"] = out / "incident_note.md"
    paths["incident_note"].write_text((summarizer or get_summarizer()).summarize(ctx))

    for fmt in alert_formats:
        alert = build_alert(decision, dataset, run_id, fmt, note_url=None)
        if fmt == "pagerduty":  # keep committed sample output stable
            alert["payload"]["timestamp"] = stamp
        paths[f"alert_{fmt}"] = out / f"alert_{fmt}.json"
        paths[f"alert_{fmt}"].write_text(json.dumps(alert, indent=2, default=str) + "\n")
        if decision.is_open:
            send_alert(alert)
    log_event(logger, "outputs_written", out_dir=out, files=len(paths))
    return paths
