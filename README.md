# Mini Codex Reliability Lab

Tiny Python repo for checking how a tool-using coding agent behaves on a handful
of small, local bugfix tasks.

This is not meant to be a serious benchmark. The workspaces are intentionally
small so it is easy to inspect traces, reproduce mistakes, and change one piece
of the loop without dragging in a lot of unrelated code.

## Running

```bash
python3 -m pip install -r requirements.txt
python3 -m pytest -q
```

Single task:

```bash
export ANTHROPIC_API_KEY=...

python main.py \
  --issue "binary_search returns the wrong index when it finds the target" \
  --workspace evals/workspaces/task_001 \
  --verbose
```

Batch run:

```bash
python -m evals.run_eval --tasks task_001 --verbose
python -m evals.run_eval --output results.json
```

If you want test execution inside Docker:

```bash
python -m evals.run_eval --isolated
```

That flag copies each workspace to a temp dir and requires a reachable Docker
daemon. The runner image is built the first time it is needed.

## What Is Here

- `agent/` has the loop, plan prompt, and the five tools the model can call.
- `evals/` has the task list, the batch runner, metrics, and the toy workspaces.
- `sandbox/` has the permission model and optional Docker runner.
- `tests/` covers the framework code. Docker-specific checks are marked
  `docker` and skipped unless you opt in.

The agent can read files, search code, edit files, run tests, and inspect test
output. Test execution is limited to `pytest`, and file access stays inside the
selected workspace.

## Tasks

There are ten little workspaces under `evals/workspaces/`. Most are one-function
Python bugs: wrong comparisons, missing returns, missing guards, bad regexes,
that kind of thing. The issue text for each task lives in `evals/tasks.jsonl`.

I kept them small on purpose. If a run fails, I want it to be obvious whether
the problem was the model, the tool loop, or the sandbox path.

## Notes

I do not commit eval outputs because they depend on API credentials, model
choice, and runtime conditions. If you want numbers, generate them locally.

`reports/` just has a few working notes. `examples/` has two short writeups from
real-ish runs: one easy fix that worked, one small bug the loop still managed to
fumble.

If you want to add another task, copy the shape of one of the existing
workspaces, add tests there, and append a line to `evals/tasks.jsonl`.

The agent runner requires `ANTHROPIC_API_KEY`. The framework tests run offline with `python3 -m pytest -q`.

## License

MIT


