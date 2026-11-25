# Mini Codex Reliability Lab

Tiny Python repo for checking how a tool-using coding agent behaves on a handful
of small, local bugfix tasks.

This is not meant to be a serious benchmark. The workspaces are intentionally
small so it is easy to inspect traces, reproduce mistakes, and change one piece
of the loop without dragging in a lot of unrelated code.

## At a glance

- **Parallel execution with measured speedup, on real API calls.** 45 tasks
  (15 bugfix workspaces × 3 seeds) run with asyncio + `Semaphore(N)` on
  Docker containers:

  | Config | Wall-clock | Speedup | Efficiency | Source |
  |---|---|---|---|---|
  | Sequential | 1293.4 s (21.6 min) | 1.00× | 100 % | **real Anthropic API calls** |
  | Parallel N=4 | 354.0 s (5.9 min) | **3.65×** | **91 %** | **real Anthropic API calls** |

  Both numbers measured end-to-end on the same 45-task fleet in one run with
  `--parallel 4 --benchmark`. Raw sidecar: `reports/run_parallel_benchmark.json`
  (gitignored; the numbers in this table are the artifact).

  Synthetic cross-check — same orchestrator, same 45 task durations replayed
  with `time.sleep` (releases the GIL like a blocking HTTP call) to isolate
  the orchestrator overhead from API tail-latency:

  | Concurrency | Synthetic wall | Speedup | Efficiency |
  |---|---|---|---|
  | N=1 | 135.0 s | 1.00× | 100 % |
  | N=2 | 68.9 s | 1.96× | 98 % |
  | N=4 | 34.9 s | **3.87×** | **97 %** |
  | N=8 | 18.6 s | 7.27× | 91 % |

  The 3.65× real vs 3.87× synthetic at N=4 is the ~6 % tail-latency gap the
  real API shows and the orchestrator-only replay doesn't. Reproduce with
  `python -m evals.benchmark_parallel`.

- **Per-task timeout and cancellation.** `asyncio.wait_for(run_task, timeout=S)`
  fires a `TimeoutError` at the caller boundary; the runner flips a
  `threading.Event` the agent loop checks before each SDK call (cooperative
  cancellation, since Python can't pre-empt a running thread), and kills any
  Docker container tagged by this run via
  `docker kill --filter label=<run-uuid>`. SIGINT does the same for every
  in-flight task at once. The cancelled run returns a shaped-like-the-others
  dict with `failure_category="timeout"`, which the classifier maps to
  `root_cause="harness_error"` so it never pollutes the model-failure bucket.

- **138 framework tests** green under `pytest -W error`, including 9 that
  pin the parallel runner's contract: semaphore bound, wall-clock-shorter-
  than-sum, cancel-event-reaches-thread, unique-container-label per run.

- **Five classifier bugs self-caught across the day** are the real empirical
  result — the taxonomy kept flagging edge cases in itself. Full writeup in
  [reports/failure_breakdown.md](reports/failure_breakdown.md).

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

Batch run (Docker sandbox is the default):

```bash
python -m evals.run_eval --output reports/run.json
```

For faster iteration while developing the loop itself, drop into local mode:

```bash
python -m evals.run_eval --local --output reports/run.json
```

`--local` runs pytest on the host Python. Use it to debug the harness, not
to report numbers.

### Multi-seed and variance

```bash
python -m evals.run_eval --repeats 3 --temperature 0.2 --output reports/run.json
```

With `--repeats > 1` each task runs N times under different seeds. The runner
reports mean ± stddev per task. See the caveats below on what N=3 actually
tells you.

### Parallel execution

```bash
# Run 4 (task, seed) pairs at a time, with a 60-second per-task wall-clock budget:
python -m evals.run_eval --parallel 4 --per-task-timeout 60 --output reports/run.json

# Measure speedup vs sequential on the same fleet (doubles API spend):
python -m evals.run_eval --parallel 4 --benchmark --output reports/run.json
```

- `--parallel N` bounds the number of concurrent `(task, seed)` workers AND
  therefore the number of Docker containers alive at once (`asyncio.Semaphore`).
  Default is 1 → sequential; bit-identical to the pre-parallel code path.
- `--per-task-timeout SECONDS` fires `asyncio.wait_for`. On timeout the agent's
  cancellation event is set so the loop exits before its next SDK call, and
  Docker containers tagged by that run are killed via `docker kill`. The result
  is shaped like a normal failure with `failure_category="timeout"`.
- SIGINT (Ctrl-C) sets a global stop flag; in-flight tasks are cancelled, the
  gather is awaited to let them return their partial outcomes, and any stray
  labelled containers are cleaned up.
- `--benchmark` runs the "other mode" too (sequential if you ran parallel, and
  vice versa) and prints `speedup = sequential_s / parallel_s`.

What this does NOT do: it can't forcibly kill a running agent thread mid-SDK
call — Python threads aren't pre-emptible. Cancellation is cooperative at
iteration boundaries. In practice the per-task timeout returns to the caller
within a few seconds regardless; the thread exits at its next loop iteration.

### Ablations

Each component of the harness has an off switch:

```bash
python -m evals.run_eval --no-planner       # skip the planner step
python -m evals.run_eval --no-resolver      # don't resolve filenames in the issue
python -m evals.run_eval --no-inspect-error # hide the inspect_error tool
python -m evals.run_eval --exposed-tools read_file,edit_file,run_tests
python -m evals.run_eval --preset weak-agent
```

`weak-agent` is the predefined minimal configuration: same model, same
temperature, no planner, no resolver, only `read_file` + `edit_file` +
`run_tests`. It is the fair LLM-level baseline against the full loop.

The non-LLM baseline — regex mutations over the referenced function — lives in
`evals.naive_baseline` and runs with:

```bash
python -m evals.run_baseline --output reports/baseline.json
```

**Baseline determinism caveat.** The baseline applies mutations in a fixed
order and stops at the first one that turns the suite green. Reordering the
mutation list changes which task passes with which rule; the ordering in
`evals/naive_baseline.py::_mutations_for_body` is intentional (cheap
structural flips first, semantically-loaded refactors last) but any report
should cite the commit SHA of the baseline it used, not just "the naive
baseline".

## What is here

- `agent/` — the loop, planner, path resolver, and the five tools the model
  can call.
- `evals/` — tasks list, batch runner, naive baseline, metrics, the failure
  taxonomy (coarse `classify_failure` + the three-way `classify_root_cause`
  + reference-based `classify_against_reference`), and the toy workspaces.
- `sandbox/` — permission policy + argv-based Docker runner.
- `tests/` — framework coverage. Docker-specific checks are marked `docker`
  and skipped unless you opt in with `-m docker`.

The agent can read files, search code, edit files, run tests, and inspect
test output. Test execution is limited to `pytest` with no shell semantics
(argv only; `shlex.split`, no `shell=True`), and file access stays inside
the selected workspace.

### Instrumentation the eval adds

Every run records, in addition to the usual tool counts:

- **Failed tool calls, split from feedback failures.** A pytest run with red
  tests is *not* a tool failure; pytest returning "no tests collected" or a
  file-not-found error is. See `agent.memory.record_tool_call`.
- **Files read before the first successful edit.** Used by the three-way
  classifier to detect "edited without reading".
- **Test-file modifications.** Any patch under `tests/` or on a `conftest.py`
  is tracked separately. A "pass" that patched a test is flagged as
  `test_tampering` and does not count as a clean success.
- **Final file contents for reference files.** The reference check verifies
  `must_contain` tokens against the actual post-fix file, not against the
  `+` lines of the diff.

## Tasks

Fifteen workspaces under `evals/workspaces/`. Each entry in `tasks.jsonl`
carries a `source` field so bias is visible:

- task_001–task_010 (`author_designed`): one-function Python bugs — wrong
  comparisons, missing returns, missing guards, bad regexes.
- task_011 (`author_designed`): cross-file — the issue cites `discount.py`,
  the bug is in `pricing.py`.
- task_012 (`author_designed`): trap test — a pre-existing passing test
  encodes the buggy behavior; the agent must update both source and test.
- task_013 (`author_designed`): config bug — the fault is in
  `tests/conftest.py`, not in `src/`.
- task_014–task_015 (`pattern_from_python_docs`): two Python gotchas drawn
  from the CPython docs (mutable default argument, shallow-vs-deep copy).
  The function names and tests are original; the bug shape and the
  intended fix idiom come from the cited section of the stdlib docs.

Each task has a `reference` block declaring which files the fix should
touch, optional `must_contain` / `must_not_contain` tokens, and a
`forbid_test_edits` flag that turns test modifications into a failure for
that task.

**Bias caveat.** 13 of 15 tasks are designed by the author, who knows the
intended fix pattern in advance. The `source` field is explicit so a reader
can weight the results. Pulling tasks from an external benchmark
(SWE-bench-mini, documented Flask/Django bugfix commits) is future work and
is noted in `reports/next_steps.md`.

## What the numbers do and don't mean

- **A pass rate over 1 seed is a single sample.** With `--repeats 3`, a task
  that solves 2/3 is "two out of three", not a reliability figure; under
  N≈10 you don't have statistics, you have examples. Report means with
  stddev and treat anything within a seed of 100% as noise-limited.
- **The eval only varies model sampling.** The issue text, the workspace
  state, and the bug itself are fixed across seeds. A real reliability
  benchmark varies the input distribution too — paraphrased issues, bug
  variants, confounding files.
- **The 13 workspaces are hand-crafted.** The harder ones (011–013) in
  particular were designed knowing what pattern they test. Use them to
  surface specific failure modes, not to claim coverage.

## Reports

`reports/` holds working notes and the output of a run; eval numbers are
regenerated locally so nothing committed carries stale API costs.

The agent runner requires `ANTHROPIC_API_KEY`. The framework tests run offline with `python3 -m pytest -q`.

## License

MIT


