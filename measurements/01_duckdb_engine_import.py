"""Measurement 01."""

import datetime

import duckdb
from sqlalchemy import create_engine, text


def main():
    """Main."""
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    out = []
    out.append(f"Date: {date_str}")
    out.append("Measurement: duckdb_engine 0.17.0 against DuckDB 1.5.6")
    try:
        engine = create_engine("duckdb:///:memory:")
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1")).scalar()
            out.append("Result: Success, it imports and opens a connection.")
            out.append(f"Output of SELECT 1: {result}")
    except duckdb.Error as e:
        out.append(f"Result: Failure")
        out.append(f"Exception: {repr(e)}")

    with open("measurements/01_duckdb_engine_import.md", "w") as f:
        f.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
