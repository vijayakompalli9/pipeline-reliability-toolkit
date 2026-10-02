"""End-to-end demo on seeded synthetic payments data with injected defects.

    python -m reliability.demo [--out docs/sample_output] [--data-dir data/demo]

Injected defects (fictional Northwind Mutual Insurance billing feed):
  1. ``branch_code`` drifts INTEGER -> VARCHAR ("BR-0123") in the incoming extract
  2. 3% of source rows silently missing from the target (plus a few explained rejects)
  3. duplicate rows in the target from a non-idempotent retry
  4. late file: newest event is ~30h older than the run's as-of time
  5. a handful of payments reference policies absent from the policy dimension
"""

from __future__ import annotations

import argparse
import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from reliability.breaker import EXIT_CIRCUIT_OPEN, CircuitBreaker
from reliability.contracts import load_contract
from reliability.lineage import Lineage
from reliability.logging_utils import configure_logging, log_event
from reliability.pipeline import check_contract, reconcile_sources, write_outputs
from reliability.reconcile import load_recon_config

logger = logging.getLogger("reliability.demo")

SEED = 20260930
AS_OF = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)
RUN_ID = "demo-2026-09-30"


@dataclass(frozen=True)
class DefectSpec:
    rows: int = 5000
    policies: int = 1200
    missing_pct: float = 3.0
    rejected: int = 20
    duplicates: int = 25
    orphans: int = 6
    late_hours: float = 30.0


def generate(data_dir: str | Path, spec: DefectSpec | None = None, seed: int = SEED) -> dict[str, Path]:
    """Write policies, payments_source, payments_target and payments_rejected CSVs."""
    spec = spec or DefectSpec()
    rng = random.Random(seed)  # noqa: S311 - synthetic data, not security
    out = Path(data_dir)
    out.mkdir(parents=True, exist_ok=True)
    policy_ids = [f"POL-{i:06d}" for i in range(1, spec.policies + 1)]
    policies = pd.DataFrame({
        "policy_id": policy_ids,
        "line_of_business": [rng.choice(["AUTO", "HOME", "LIFE", "COMMERCIAL"]) for _ in policy_ids],
    })

    newest = AS_OF - timedelta(hours=spec.late_hours)
    rows = []
    for i in range(1, spec.rows + 1):
        ptype = rng.choices(["PREMIUM", "CLAIM_PAYOUT", "REFUND"], weights=[80, 15, 5])[0]
        amount = round(rng.lognormvariate(6.0 if ptype == "PREMIUM" else 8.0, 0.8), 2)
        rows.append({
            "payment_id": f"PAY-{i:07d}",
            "policy_id": rng.choice(policy_ids),
            "branch_code": f"BR-{rng.randint(100, 480):04d}",  # defect 1: was an integer
            "payment_type": ptype,
            "amount": min(amount, 240000.0),
            "currency": rng.choices(["USD", "CAD"], weights=[92, 8])[0],
            "status": rng.choices(["SETTLED", "PENDING", "FAILED", "REVERSED"], weights=[85, 8, 5, 2])[0],
            "event_ts": newest - timedelta(minutes=rng.randint(0, 24 * 60)),
        })
    rows[-1]["event_ts"] = newest  # defect 4: newest record pinned to the late cutoff
    for idx in rng.sample(range(spec.rows), spec.orphans):  # defect 5
        rows[idx]["policy_id"] = f"POL-9{rng.randint(10000, 99999)}"
    source = pd.DataFrame(rows)

    n_missing = round(spec.rows * spec.missing_pct / 100)
    dropped = rng.sample(range(spec.rows), n_missing + spec.rejected)
    rejected_idx, missing_idx = dropped[: spec.rejected], dropped[spec.rejected:]  # defect 2
    rejected = source.iloc[rejected_idx][["payment_id"]].assign(
        reject_reason="failed_ledger_validation", rejected_at=AS_OF.replace(tzinfo=None))
    keep = source.drop(index=rejected_idx + missing_idx)
    dupes = keep.sample(n=spec.duplicates, random_state=seed)  # defect 3
    target = pd.concat([keep, dupes]).sort_values("payment_id").reset_index(drop=True)

    paths = {}
    for name, frame in {"policies": policies, "payments_source": source,
                        "payments_target": target, "payments_rejected": rejected}.items():
        paths[name] = out / f"{name}.csv"
        frame.to_csv(paths[name], index=False)
    log_event(logger, "demo_data_generated", dir=out, source_rows=len(source), target_rows=len(target),
              missing=len(missing_idx), rejected=len(rejected), duplicates=spec.duplicates)
    return paths


def run(out_dir: str | Path, data_dir: str | Path, config_dir: str | Path = "configs") -> int:
    """Generate data, run every component, write outputs. Returns the gate's exit code."""
    cfg_dir = Path(config_dir)
    paths = generate(data_dir)
    contract = load_contract(cfg_dir / "contracts" / "payments_source.yaml")
    recon = load_recon_config(cfg_dir / "reconciliation" / "payments.yaml")

    results = check_contract(
        contract, {"payments_source": paths["payments_source"], "policies": paths["policies"]}, AS_OF)
    results += reconcile_sources(recon, {k: paths[k] for k in ("payments_source", "payments_target",
                                                               "payments_rejected")})
    decision = CircuitBreaker().evaluate(results)
    written = write_outputs(out_dir, RUN_ID, results, decision, AS_OF, Lineage.load(cfg_dir / "lineage.yaml"),
                            ("slack", "teams", "pagerduty"))
    print(f"circuit={decision.state.value} exit_code={decision.exit_code} :: {decision.reason()}")
    for key, path in written.items():
        print(f"wrote {key:<16} {path}")
    return decision.exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="docs/sample_output")
    parser.add_argument("--data-dir", default="data/demo")
    parser.add_argument("--config-dir", default="configs")
    parser.add_argument("--strict", action="store_true", help="exit 2 when the circuit opens (CI-style)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    configure_logging(args.log_level)
    code = run(args.out, args.data_dir, args.config_dir)
    if code == EXIT_CIRCUIT_OPEN and not args.strict:
        print("demo: circuit is OPEN as intended (defects were injected); pass --strict to propagate exit 2")
        return 0
    return code


if __name__ == "__main__":
    raise SystemExit(main())
