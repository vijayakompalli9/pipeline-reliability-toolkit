"""Load CSV / Parquet files or pandas DataFrames into DuckDB tables."""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pandas as pd

from reliability.exceptions import DataSourceError

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote_ident(name: str) -> str:
    """Quote a SQL identifier for DuckDB."""
    return '"' + name.replace('"', '""') + '"'


def connect(database: str = ":memory:") -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection pinned to UTC so TIMESTAMPTZ values compare consistently."""
    con = duckdb.connect(database)
    con.execute("SET TimeZone = 'UTC'")
    return con


def register(con: duckdb.DuckDBPyConnection, name: str, source: str | Path | pd.DataFrame) -> None:
    """Materialize ``source`` as table ``name`` in the DuckDB connection."""
    if not _IDENT.match(name):
        raise DataSourceError(f"invalid table name {name!r}; use letters, digits, underscore")
    if isinstance(source, pd.DataFrame):
        con.register("_tmp_df", source)
        con.execute(f"CREATE OR REPLACE TABLE {quote_ident(name)} AS SELECT * FROM _tmp_df")
        con.unregister("_tmp_df")
        return
    path = Path(source)
    if not path.is_file():
        raise DataSourceError(f"data file not found: {path}")
    reader = "read_parquet" if path.suffix.lower() == ".parquet" else "read_csv_auto"
    literal = str(path).replace("'", "''")
    try:
        con.execute(f"CREATE OR REPLACE TABLE {quote_ident(name)} AS SELECT * FROM {reader}('{literal}')")
    except duckdb.Error as exc:
        raise DataSourceError(f"could not load {path}: {exc}") from exc


def parse_data_args(values: list[str], default_name: str) -> dict[str, str]:
    """Parse CLI ``--data`` values: ``path`` (bound to default_name) or ``name=path``."""
    mapping: dict[str, str] = {}
    for value in values:
        name, sep, path = value.partition("=")
        if sep:
            mapping[name] = path
        else:
            mapping[default_name] = value
    return mapping


def observed_schema(con: duckdb.DuckDBPyConnection, table: str) -> dict[str, str]:
    """Return ``{column_name: duckdb_type}`` for a table."""
    rows = con.execute(f"DESCRIBE {quote_ident(table)}").fetchall()
    return {row[0]: row[1] for row in rows}
