Date: 2026-10-17
Measurement: One-writer rule (read-write and read-only concurrently)
Result: A second process CANNOT open the same file read-only.
DuckDB's exact message: IO Error: Could not set lock on file "/app/test_one_writer.duckdb": Conflicting lock is held in /home/jules/.pyenv/versions/3.10.20/bin/python3.10 (PID 82600). See also https://duckdb.org/docs/stable/connect/concurrency
