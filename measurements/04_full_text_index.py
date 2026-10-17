"""Measurement 04."""

import datetime
import time

import duckdb


def run_size(conn, size):
    """Run size."""
    conn.execute("DROP TABLE IF EXISTS docs;")
    conn.execute(
        f"CREATE TABLE docs AS SELECT id, 'this is some short text document number ' || id::VARCHAR AS txt FROM generate_series(1, {size}) g(id);"
    )

    start_time = time.time()
    conn.execute("PRAGMA create_fts_index('docs', 'id', 'txt');")
    end_time = time.time()

    conn.execute("PRAGMA drop_fts_index('docs');")
    return end_time - start_time


def main():
    """Main."""
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    out = []
    out.append(f"Date: {date_str}")
    out.append("Measurement: Full-text index (PRAGMA create_fts_index)")

    try:
        conn = duckdb.connect(":memory:")
        for size in [10000, 100000, 1000000]:
            print(f"Running size {size}...")
            try:
                wall_time = run_size(conn, size)
                out.append(f"Size {size}: {wall_time:.4f} seconds")
            except duckdb.Error as e:
                out.append(f"Size {size}: Failed - {e}")
                break
    except duckdb.Error as e:
        out.append(f"Fatal error: {e}")

    with open("measurements/04_full_text_index.md", "w") as f:
        f.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
