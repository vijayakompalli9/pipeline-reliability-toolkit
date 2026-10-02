from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from reliability.contracts import parse_contract
from reliability.io import connect, register

AS_OF = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def con():
    c = connect()
    yield c
    c.close()


@pytest.fixture
def orders() -> pd.DataFrame:
    return pd.DataFrame({
        "order_id": ["O-1", "O-2", "O-3", "O-4"],
        "customer_id": ["C-1", "C-2", "C-2", "C-3"],
        "qty": [1, 5, 10, 2],
        "status": ["OPEN", "SHIPPED", "OPEN", "CLOSED"],
        "created_at": pd.to_datetime(["2026-09-30 08:00", "2026-09-30 09:00", "2026-09-30 10:00",
                                      "2026-09-30 11:00"]),
    })


@pytest.fixture
def loaded(con, orders):
    register(con, "orders", orders)
    register(con, "customers", pd.DataFrame({"customer_id": ["C-1", "C-2", "C-3"]}))
    return con


def make_contract(rules: list[dict], schema: list[dict] | None = None, **extra):
    schema = schema or [
        {"name": "order_id", "type": "string", "nullable": False},
        {"name": "customer_id", "type": "string"},
        {"name": "qty", "type": "integer"},
        {"name": "status", "type": "string"},
        {"name": "created_at", "type": "timestamp"},
    ]
    return parse_contract({"dataset": "orders", "schema": schema, "rules": rules, **extra})
