Date: 2026-10-18T12:44:30.600272+00:00
Measurement: exact cosine scan over distinct synthetic FLOAT[1024] vectors
Machine: Lenovo-Y520-15IKBN; platform: Linux-7.0.0-31-generic-x86_64-with-glibc2.39
Python: 3.12.3; DuckDB: 1.5.6
Queries per size: 3; target: stored vector id=1
Population: deterministic hash(id * 1024 + dimension) mapped to [-1, 1].
Distinct hashes prove distinct vectors; hash collisions cause refusal.
These synthetic unfiltered scans do not establish real-model retrieval quality
or a production scan/index threshold. Prior cloud timings used repeated identical vectors
and are not comparable. Peak RSS is process-wide high-water memory, not per-size allocation.
Size 10000: rows=10000; distinct vector hashes=10000; query seconds=[0.02956250391434878, 0.026578091084957123, 0.025236506946384907]; peak RSS KiB=239284
Size 100000: rows=100000; distinct vector hashes=100000; query seconds=[0.2000644049840048, 0.19696173607371747, 0.1996798129985109]; peak RSS KiB=629052
Size 1000000: rows=1000000; distinct vector hashes=1000000; query seconds=[0.5423394759418443, 0.5399246490560472, 0.5350674609653652]; peak RSS KiB=4584588
