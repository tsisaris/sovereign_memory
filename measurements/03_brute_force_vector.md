Date: 2026-10-17T21:43:37.585302+00:00
Measurement: exact cosine scan over distinct synthetic FLOAT[1024] vectors
Machine: Lenovo-Y520-15IKBN; platform: Linux-7.0.0-31-generic-x86_64-with-glibc2.39
Python: 3.10.20; DuckDB: 1.5.6
Queries per size: 3; target: stored vector id=1
Population: deterministic hash(id * 1024 + dimension) mapped to [-1, 1].
Distinct hashes prove distinct vectors; hash collisions cause refusal.
These synthetic unfiltered scans do not establish real-model retrieval quality
or a production scan/index threshold. Prior cloud timings used repeated identical vectors
and are not comparable. Peak RSS is process-wide high-water memory, not per-size allocation.
Size 10000: rows=10000; distinct vector hashes=10000; query seconds=[0.1630795799428597, 0.170525006018579, 0.15143282106146216]; peak RSS KiB=139072
Size 100000: rows=100000; distinct vector hashes=100000; query seconds=[0.33542905503418297, 0.3283257409930229, 0.3269247099524364]; peak RSS KiB=547124
Size 1000000: rows=1000000; distinct vector hashes=1000000; query seconds=[0.6760712679242715, 0.6407045490341261, 0.6596022190060467]; peak RSS KiB=4519388
