"""Example Airflow DAG: the reliability toolkit gates the publish step of a load.

    extract_payments -> load_staging -> reliability_gate -> publish_reporting_marts

``reliability_gate`` raises ``CircuitOpenError`` when any critical check fails, so
Airflow marks the task failed and ``publish_reporting_marts`` never runs. The
gate is a plain function, so it is unit-tested without Airflow installed; the
DAG object is only built when Airflow is importable.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from reliability.breaker import CircuitBreaker, build_alert, send_alert
from reliability.contracts import load_contract
from reliability.pipeline import check_contract, reconcile_sources
from reliability.reconcile import load_recon_config

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"
DATA_DIR = Path("/opt/data/payments")  # placeholder landing zone


def reliability_gate(data_dir: str | Path = DATA_DIR, as_of: datetime | None = None, **_: Any) -> dict[str, Any]:
    """Run contract + reconciliation checks; raise CircuitOpenError to block downstream tasks."""
    data = Path(data_dir)
    contract = load_contract(CONFIG_DIR / "contracts" / "payments_source.yaml")
    recon = load_recon_config(CONFIG_DIR / "reconciliation" / "payments.yaml")
    results = check_contract(
        contract, {"payments_source": data / "payments_source.csv", "policies": data / "policies.csv"}, as_of
    )
    results += reconcile_sources(recon, {n: data / f"{n}.csv" for n in (recon.source, recon.target, recon.rejected)})
    breaker = CircuitBreaker()
    decision = breaker.evaluate(results)
    if decision.is_open:
        send_alert(build_alert(decision, "payments", run_id="airflow", fmt="slack"))  # dry-run unless configured
    breaker.guard(results)
    return {"state": decision.state.value, "warnings": len(decision.warnings)}


try:  # Airflow is optional: the toolkit and its tests never require it.
    from airflow import DAG

    try:
        from airflow.providers.standard.operators.python import PythonOperator  # Airflow 3
    except ImportError:
        from airflow.operators.python import PythonOperator  # Airflow 2
except ImportError:
    DAG = None

if DAG is not None:
    with DAG(
        dag_id="payments_load_with_reliability_gate",
        start_date=datetime(2026, 1, 1),
        schedule="0 6 * * *",
        catchup=False,
        tags=["reliability", "payments"],
    ) as dag:
        extract = PythonOperator(task_id="extract_payments", python_callable=lambda: None)
        load_staging = PythonOperator(task_id="load_staging", python_callable=lambda: None)
        gate = PythonOperator(task_id="reliability_gate", python_callable=reliability_gate)
        publish = PythonOperator(task_id="publish_reporting_marts", python_callable=lambda: None)
        extract >> load_staging >> gate >> publish
