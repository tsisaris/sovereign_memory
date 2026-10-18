"""Measure exact vector scans using distinct synthetic vectors on this machine."""

import argparse
import datetime
import platform
import resource
import time
from pathlib import Path

import duckdb


def run_size(conn, size, repeats):
    """Build the requested synthetic population and return checked scan timings."""
    if size < 1 or repeats < 1:
        raise ValueError("size and repeats must be positive integers")
    if size > (2**63 // 1024) - 1:
        raise ValueError("size exceeds the BIGINT range for packed vector coordinates")
    conn.execute("DROP TABLE IF EXISTS vectors")
    conn.execute(
        "CREATE TABLE vectors AS SELECT id, "
        "list_transform(range(1024), dimension -> "
        "(2.0 * hash(id * 1024 + dimension)::DOUBLE / 18446744073709551615.0 - 1.0)::FLOAT)"
        "::FLOAT[1024] AS v FROM generate_series(1, ?) g(id)",
        [size],
    )
    rows, distinct_hashes = conn.execute(
        "SELECT count(*), count(DISTINCT hash(v)) FROM vectors"
    ).fetchone()
    if (rows, distinct_hashes) != (size, size):
        raise ValueError(
            f"invalid benchmark population: rows={rows}, distinct vector hashes={distinct_hashes}, "
            f"expected both {size}"
        )
    target = conn.execute("SELECT v FROM vectors WHERE id = 1").fetchone()[0]
    timings = []
    for _ in range(repeats):
        started = time.perf_counter()
        result = conn.execute(
            "SELECT id FROM vectors ORDER BY array_cosine_distance(v, ?::FLOAT[1024]), id LIMIT 10",
            [target],
        ).fetchall()
        elapsed = time.perf_counter() - started
        if len(result) != min(size, 10) or result[0][0] != 1:
            raise ValueError(f"incorrect nearest-neighbor result: {result!r}")
        timings.append(elapsed)
    return rows, distinct_hashes, timings


def main():
    """Record all specified sizes and runtime provenance, raising any measurement failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, required=True, help="Queries per population size")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    output = Path(__file__).with_suffix(".md")
    lines = [
        f"Date: {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
        "Measurement: exact cosine scan over distinct synthetic FLOAT[1024] vectors",
        f"Machine: {platform.node()}; platform: {platform.platform()}",
        f"Python: {platform.python_version()}; DuckDB: {duckdb.__version__}",
        f"Queries per size: {args.repeats}; target: stored vector id=1",
        "Population: deterministic hash(id * 1024 + dimension) mapped to [-1, 1].",
        "Distinct hashes prove distinct vectors; hash collisions cause refusal.",
        "These synthetic unfiltered scans do not establish real-model retrieval quality",
        "or a production scan/index threshold. Prior cloud timings used repeated identical vectors",
        "and are not comparable. Peak RSS is process-wide high-water memory, not per-size allocation.",
    ]
    try:
        with duckdb.connect(":memory:") as conn:
            for size in (10000, 100000, 1000000):
                print(f"Building and querying {size} distinct vectors...", flush=True)
                rows, distinct_hashes, timings = run_size(conn, size, args.repeats)
                line = (
                    f"Size {size}: rows={rows}; distinct vector hashes={distinct_hashes}; "
                    f"query seconds={timings}; peak RSS KiB={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}"
                )
                lines.append(line)
                print(line, flush=True)
    except Exception as exc:
        lines.append(f"FAILED: {type(exc).__name__}: {exc}")
        raise
    finally:
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Measurement output: {output}", flush=True)


if __name__ == "__main__":
    main()
