"""Measurement 03."""

import datetime
import time

import duckdb


def run_size(conn, size):
    """Run size."""
    conn.execute("DROP TABLE IF EXISTS vectors;")
    conn.execute("CREATE TABLE vectors (id INT, v FLOAT[1024]);")

    conn.execute(f"""
        INSERT INTO vectors
        SELECT g.id, list_transform(range(1, 1025), x -> random() + g.id*0)::FLOAT[1024]
        FROM range(1, {size} + 1) g(id);
    """)
    target = [0.5] * 1024

    start_time = time.time()
    conn.execute(
        "SELECT id FROM vectors ORDER BY array_cosine_distance(v, ?::FLOAT[1024]) LIMIT 10;",
        [target],
    ).fetchall()
    end_time = time.time()
    return end_time - start_time


def main():
    """Main."""
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    out = []
    out.append(f"Date: {date_str}")
    out.append("Measurement: Brute-force vector scan (ORDER BY array_cosine_distance)")

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

    with open("measurements/03_brute_force_vector.md", "w") as f:
        f.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
