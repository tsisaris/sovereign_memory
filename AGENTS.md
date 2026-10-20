# AGENTS.md — sovereign_memory

You are working in the GitHub repository of the portable project sovereign_memory.
Its purpose, in the owner's words: One large, connected, seamless local memory system on DuckDB: every useful kind of memory welded into one suite that works with DuckDB, a true superset of Letta, Zep/Graphiti, Cognee and Mem0, modular so new kinds of memory can be added as they appear, free and open for everyone to use and maintain.

## First, set up

Run `bash jules_setup.sh` before anything else. It installs this project's
packages and its checkers. Install nothing else by hand.

## Before you submit, run the same checks GitHub runs

    uv run pre-commit run --all-files
    uv run deptry src tests
    uv run pytest

All three must pass, and deptry must not print a line starting with "Assuming". Your pull request is checked again on GitHub, and a
failure there comes back to you to fix in this same task.

## How you work

- Do not stop to ask about a routine choice. Decide by the task and this file,
  and state each decision in the pull request description. When the task and
  this file truly contradict each other, or a step needs the owner's
  permission, say exactly that in the pull request description and finish
  every part that does not depend on it.
- Every test runs against throwaway copies of any database and shared files,
  made under pytest's tmp_path. No test may reach the real ones.

## What you deliver

- Working code, checked with the existing checks above. Add, change or delete
  a test only when the task names that test; otherwise list in the pull
  request description any test you find missing: the behaviour it would
  cover, why the existing checks miss it, and the smallest test that would.
  Code only: no reports, audits, plans or design documents unless the task
  asks for one by name.
- No placeholders, stubs, TODOs, invented data, or errors caught and ignored.
  If you cannot finish something, stop and say exactly what is missing in the
  pull request description. Never make a failure look like a success.
- Every module, class and function has a docstring in plain words: what it is
  for, what goes in, what comes out, and what it never does.
- A test the task names checks real behaviour from input to output. It lists
  every state each input can be in and tests each one, as rows of one table
  run by one test. A test that only imports a module or checks a constant
  does not count.
- No path written into code: derive every path from the file's own location.
  No tunable number written into code: read it at run time from its declared
  source.
- A new package goes into `pyproject.toml` under `[project] dependencies` as
  `name==version`, then run `uv lock` and commit `uv.lock` with it. deptry
  refuses an import that is not declared.

## Never

- Never edit `data/sovereign/`, `.github/`, `.pre-commit-config.yaml`,
  `jules_setup.sh`, this file, or the checker sections of `pyproject.toml`.
  The mirror folders are read-only copies whose real home is elsewhere.
- Never weaken, skip or silence a check: no `noqa`, `type: ignore`, `nosec`,
  `pylint: disable`, skipped or deleted tests, or loosened settings.
- Never write into `local/`; it is never committed.
