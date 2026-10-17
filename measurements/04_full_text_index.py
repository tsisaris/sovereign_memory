"""Measurement 04."""

import datetime
import time

import duckdb


def run_size(size):
    """Run size."""
    # use a new connection per size to avoid index clash
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE docs (id INT, text_content VARCHAR);")

    print(f"Generating Python data for size {size}...")
    conn.execute(f"""
        INSERT INTO docs
        SELECT id, 'This is a short text document number ' || id::VARCHAR || ' with some keywords for full text search index.'
        FROM generate_series(1, {size}) g(id);
    """)

    start_time = time.time()
    conn.execute("PRAGMA create_fts_index('docs', 'id', 'text_content');")
    end_time = time.time()
    return end_time - start_time


def main():
    """Main."""
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    out = []
    out.append(f"Date: {date_str}")
    out.append("Measurement: Full-text index build (PRAGMA create_fts_index)")

    try:
        for size in [10000, 100000, 1000000]:
            print(f"Running size {size}...")
            try:
                wall_time = run_size(size)
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
