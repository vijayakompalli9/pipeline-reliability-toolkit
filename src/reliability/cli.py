"""Command line interface.

    reliability check --contract configs/contracts/payments_source.yaml \
        --data data/payments_source.csv --data policies=data/policies.csv
    reliability reconcile --config configs/reconciliation/payments.yaml \
        --source data/payments_source.csv --target data/payments_target.csv --rejected data/payments_rejected.csv

Exit codes: 0 circuit closed, 2 circuit open (blocking failure), 3 tool/config error.
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import UTC, datetime

from reliability.breaker import EXIT_TOOL_ERROR, CircuitBreaker
from reliability.contracts import load_contract
from reliability.exceptions import ReliabilityError
from reliability.io import parse_data_args
from reliability.lineage import Lineage
from reliability.logging_utils import configure_logging, log_event
from reliability.pipeline import check_contract, reconcile_sources, write_outputs
from reliability.reconcile import load_recon_config
from reliability.results import CheckResult, Severity

logger = logging.getLogger("reliability.cli")


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--fail-on", choices=["critical", "warn"], default="critical",
                   help="lowest severity that opens the circuit (default: critical)")
    p.add_argument("--out-dir", help="write results.json, report.md, incident_note.md, alert JSON here")
    p.add_argument("--lineage", help="lineage YAML used for blast radius in the incident note")
    p.add_argument("--alert-format", action="append", choices=["slack", "teams", "pagerduty"],
                   help="alert payload shape(s) to write (default: slack)")
    p.add_argument("--run-id", default=None)
    p.add_argument("--log-level", default="WARNING")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reliability", description="Config-driven data reliability gate")
    sub = parser.add_subparsers(dest="command", required=True)

    chk = sub.add_parser("check", help="schema drift + data quality rules from a YAML contract")
    chk.add_argument("--contract", required=True)
    chk.add_argument("--data", action="append", required=True,
                     help="PATH for the contract dataset, or NAME=PATH for referenced tables (repeatable)")
    chk.add_argument("--as-of", help="ISO timestamp used for freshness (default: now, UTC)")
    _common(chk)

    rec = sub.add_parser("reconcile", help="source-to-target reconciliation")
    rec.add_argument("--config", required=True)
    rec.add_argument("--source", required=True)
    rec.add_argument("--target", required=True)
    rec.add_argument("--rejected", help="rejected-records file (if the config names a rejected table)")
    _common(rec)
    return parser


def _print(results: list[CheckResult]) -> None:
    for r in results:
        status = "PASS" if r.passed else ("ERROR" if r.error else "FAIL")
        col = f"[{r.column}]" if r.column else ""
        print(f"{status:<5} {r.severity.value:<8} {r.category:<14} {r.dataset}.{r.check}{col}: {r.message}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level)
    run_id = args.run_id or f"{args.command}-{uuid.uuid4().hex[:8]}"
    try:
        as_of = datetime.now(UTC)
        if args.command == "check":
            contract = load_contract(args.contract)
            if args.as_of:
                as_of = datetime.fromisoformat(args.as_of)
            results = check_contract(contract, parse_data_args(args.data, contract.dataset), as_of)
        else:
            cfg = load_recon_config(args.config)
            data = {cfg.source: args.source, cfg.target: args.target}
            if cfg.rejected:
                if not args.rejected:
                    raise ReliabilityError(f"config names rejected table '{cfg.rejected}'; pass --rejected")
                data[cfg.rejected] = args.rejected
            results = reconcile_sources(cfg, data)

        decision = CircuitBreaker(Severity(args.fail_on)).evaluate(results)
        _print(results)
        print(f"circuit={decision.state.value} exit_code={decision.exit_code} :: {decision.reason()}")
        if args.out_dir:
            lineage = Lineage.load(args.lineage) if args.lineage else None
            write_outputs(args.out_dir, run_id, results, decision, as_of, lineage,
                          tuple(args.alert_format or ["slack"]))
        return decision.exit_code
    except (ReliabilityError, ValueError) as exc:
        log_event(logger, "run_failed", logging.ERROR, error=type(exc).__name__, detail=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_TOOL_ERROR


if __name__ == "__main__":
    sys.exit(main())
