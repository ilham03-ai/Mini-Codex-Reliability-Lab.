# Next steps

Open items that would move the lab from "takes itself seriously" to
"publishable". Ordered roughly by return on effort.

## Methodology

- **External task source.** 13 of 15 tasks are author-designed; 2 are
  derived from Python stdlib gotchas with attribution. Replace or
  augment with tasks mined from SWE-bench-mini, documented Flask / Django
  bugfix commits, or CPython issue tracker patterns — anything where the
  intended fix is not known to the person constructing the task.
- **Variance.** The eval only samples model-level randomness. Add
  controlled perturbations of the input distribution: paraphrased issue
  text, bug variants in the same function, confounder files that share
  symbol names.
- **N≥10 for publishable rates.** Current `--repeats 3` is anecdote scale.
  A real reliability figure wants ≥10 seeds per task; `temperature=0` is
  not a substitute because the SDK still non-deterministically batches.

## Taxonomy

- **Human-judged ground truth.** `classify_root_cause` thresholds
  (`TOOL_FAILURE_MIN_FAILED_CALLS=2`, `CONTEXT_MIN_TOOL_CALLS=3`) are
  defended by `threshold_sensitivity` but not validated. Produce a
  subset of 20–30 run traces and label them by hand; compare the
  classifier's output against the labels; report Cohen's κ.
- **Semantic diff instead of token presence.** `must_contain` catches
  the main failure modes (`return mid` vs `return mid - 1`) but misses
  subtler edits. A reference AST + the agent's final AST would give a
  real "diff distance".

## Harness

- **Ablation sweep as a single command.** Running full / no-planner /
  no-resolver / no-inspect-error / weak-agent currently requires five
  invocations and five output files. Add `python -m evals.run_sweep`
  that runs all configurations and emits one comparable table.
- **Reference agents.** Beyond the naive baseline and the weak-agent
  preset, add one more reference: the `edit_file`-only agent (no
  `read_file`, no `search_code`, no `run_tests`) that must infer the
  fix from the issue alone. Floor of what the LLM contributes.
- **Trace replay.** Save every tool call with its result; add a replay
  tool that re-runs the classifier (and future classifiers) against the
  stored trace without re-paying the API cost.

## Sandbox

- **Docker is default but opt-outable.** Investigate `firecracker-containerd`
  or Podman rootless as a step up from Docker-on-Mac. The current Docker
  path still mounts the workspace rw.
- **Resource limits per tool call.** `run_tests` has a timeout; `edit_file`
  does not. A pathological diff could lock the loop. Cap each tool at a
  hard wall-clock deadline measured from the dispatch.

## Reporting

- **Per-task failure fingerprints over time.** As the harness evolves,
  which task is the first to regress tells you more than the aggregate
  pass rate. Keep each run's JSON in `reports/history/` and emit a small
  longitudinal chart in the report.
