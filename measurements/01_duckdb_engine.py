"""Measurement 01."""

import datetime

import sqlalchemy
from sqlalchemy import create_engine


def main():
    """Main."""
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    out = []
    out.append(f"Date: {date_str}")
    out.append("Measurement: duckdb_engine 0.17.0 imports and connects to DuckDB 1.5.6")

    try:
        engine = create_engine("duckdb:///:memory:")
        with engine.connect() as conn:
            res = conn.execute(sqlalchemy.text("SELECT 1")).scalar()
            if res == 1:
                out.append("Result: Success")
            else:
                out.append(f"Result: Failed - Got {res} instead of 1")
    except sqlalchemy.exc.SQLAlchemyError as e:
        out.append(f"Result: Failed - {type(e).__name__}: {e}")

    with open("measurements/01_duckdb_engine.md", "w") as f:
        f.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
