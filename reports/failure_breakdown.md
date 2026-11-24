# Failure breakdown v2 — tool vs. context vs. model, with a baseline

**Run**: 13 tasks × 3 seeds = 39 runs, `claude-sonnet-4-6`, temperature 0.2, local sandbox, 2026-11-24.
**Harness state**: after today's fixes (filename resolver, shell-metachar rejection, test-tampering guard, reference-aware taxonomy, OR-semantics for `must_contain`, `__pycache__` cleanup, AST-based function span, ablation flags, Docker-default).
**Code**: `evals.failure_taxonomy.classify_root_cause`, `classify_against_reference`, `threshold_sensitivity`.

## Headline

| Quantity | Value |
|---|---|
| Runs | **39** (13 tasks × 3 seeds) |
| Clean passes | **39 / 39** (100%) |
| Root-cause failures (tool / context / model) | 0 / 0 / 0 |
| Test-tampering | **0** (6 raised initially, all false positives from the first classifier — see below) |
| `fix_match=correct` | **39 / 39** |
| Tool calls total | 225 |
| Tool calls refused by the tool contract | **6 / 225** (2.67%) |
| **Baseline (no-LLM, 60 lines of regex) on same tasks** | **5 / 13** (38.5%) |
| Delta agent − baseline | **+8 tasks**, 100% vs 38.5% |
| Threshold sensitivity (classifier stability under ±1) | **100%** (no failures to perturb — see caveat) |

39/39 is a clean sweep. The honest reading of that number, with everything else this report shows, is in [What the numbers do and don't say](#what-the-numbers-do-and-dont-say).

## What changed since v1

The first run was **10 tasks × 1 seed** on the previous (unfixed) harness. It reported 10/10 and 4/60 refused calls (6.7%). The v2 run is:

| | v1 (last report) | v2 (this report) |
|---|---|---|
| Tasks | 10 author-designed | 13 author-designed + 2 pattern-from-Python-docs = **15 total**, 13 used in this run (14-15 added after launch) |
| Seeds | 1 | 3 |
| Runs | 10 | 39 |
| Clean passes | 10/10 (but one was a cheat — see below) | **39/39, all fix_match=correct** |
| Refused tool calls | 4/60 (6.7%) | **6/225 (2.67%)** |
| Classifier bug caught | — | Two: (1) test-tampering was applied without consulting the task reference, flagging 6 legitimate runs as cheats; (2) `must_contain` was AND-only, flagging 4 semantically-correct patches as `partial`. Both fixed; the fix is the finding. |

The v1 `task_004` run was retroactively found to have modified the test file (`username` → `name`) instead of fixing the source. The v2 harness logs test modifications separately and the new tampering-aware classifier catches this. v2's task_004 passed cleanly 3/3 — the harness fix (filename resolver hint) removed the friction that pushed the model toward cheating.

## Per-task

All 13 tasks, 3 seeds each. μ ± σ across seeds.

| Task | Pass | Iters | Tool calls | Refused | fix_match | Notes |
|---|---|---|---|---|---|---|
| task_001 binary_search | 3/3 | 5.0 ± 0.0 | 5.0 ± 0.0 | 0.3 ± 0.6 | correct ×3 | one seed emitted `2>&1` |
| task_002 find_max | 3/3 | 4.0 ± 0.0 | 4.0 ± 0.0 | 0.0 ± 0.0 | correct ×3 | |
| task_003 fibonacci | 3/3 | 5.0 ± 0.0 | 5.7 ± 0.6 | 0.0 ± 0.0 | correct ×3 | |
| task_004 format_greeting | 3/3 | 5.0 ± 0.0 | 5.3 ± 0.6 | 0.3 ± 0.6 | correct ×3 | resolver redirected `greetings.py` → `formatter.py` ✓ |
| task_005 average on empty | 3/3 | 5.0 ± 0.0 | 6.0 ± 0.0 | 0.0 ± 0.0 | correct ×3 | |
| task_006 top_k | 3/3 | 5.0 ± 0.0 | 6.0 ± 0.0 | 0.0 ± 0.0 | correct ×3 | |
| task_007 count_occurrences | 3/3 | 5.0 ± 0.0 | 5.0 ± 0.0 | 0.0 ± 0.0 | correct ×3 | one seed used `.casefold()` (OR-ref accepts it) |
| task_008 is_valid_email | 3/3 | 5.7 ± 0.6 | 6.3 ± 1.1 | 0.3 ± 0.6 | correct ×3 | one seed emitted `2>&1` |
| task_009 sample_variance | 3/3 | 4.0 ± 0.0 | 4.3 ± 0.6 | 0.0 ± 0.0 | correct ×3 | |
| task_010 reverse_words | 3/3 | 5.0 ± 0.0 | 6.0 ± 0.0 | 0.0 ± 0.0 | correct ×3 | all used `reversed()` (OR-ref accepts it) |
| task_011 cross-file | 3/3 | 5.3 ± 0.6 | 6.0 ± 1.0 | 0.0 ± 0.0 | correct ×3 | **the issue cited `discount.py` but the bug was in `pricing.py` — resolved correctly in all 3 seeds** |
| task_012 trap test | 3/3 | 6.0 ± 0.0 | 6.0 ± 0.0 | 0.0 ± 0.0 | correct ×3 | **agent updated both source and the trap test, exactly as the task requires** |
| task_013 config bug | 3/3 | 5.7 ± 0.6 | 9.3 ± 1.5 | 1.0 ± 1.0 | correct ×3 | **hardest task: 2× `read_file` for non-existent config files, then found and fixed `tests/conftest.py`** |

## The six refused tool calls

Across 225 tool invocations, 6 were refused by the sandbox contract. All recovered, none required a re-plan.

| Task / seed | Tool | Reason |
|---|---|---|
| task_001 seed 0 | `run_tests` | `pytest ... 2>&1` — shell redirection stripped (harness message now points this out directly) |
| task_004 seed 2 | `read_file` | `tests/test_greetings.py` — same wrong basename as the issue; the resolver handled `src/greetings.py` but didn't follow through to the test counterpart |
| task_008 seed 0 | `run_tests` | `pytest ... 2>&1` — same as task_001 |
| task_013 seed 0 | `read_file` | `conftest.py` (missing path prefix) — agent exploring for the real file |
| task_013 seed 2 | `read_file` × 2 | `pytest.ini`, `pyproject.toml` — not present in the workspace |

Two of six are the `2>&1` shell idiom that the new `_sanitize_command` catches with an actionable error. The other four are the agent probing the filesystem for files that don't exist — benign exploration that costs one API call each.

## Comparison vs the naive baseline

The naive non-LLM baseline (`evals.naive_baseline`) applies regex mutations to the function named in the issue and keeps the first mutation that turns the suite green.

| Task | Agent (3 seeds) | Baseline (1 run) |
|---|---|---|
| task_001 binary_search | 3/3 | **pass** (strip `-1` from return) |
| task_002 find_max | 3/3 | **pass** (flip `<` → `>`) |
| task_003 fibonacci | 3/3 | fail (no textual mutation can add a missing `return`) |
| task_004 format_greeting | 3/3 | fail (wrong-dict-key requires semantic understanding) |
| task_005 average empty | 3/3 | fail (needs a new `raise`) |
| task_006 top_k | 3/3 | **pass** (add `reverse=True`) |
| task_007 count_occurrences | 3/3 | **pass** (`.lower()` on both sides) |
| task_008 is_valid_email | 3/3 | fail (issue doesn't name the function) |
| task_009 sample_variance | 3/3 | **pass** (`/ len` → `/ (len-1)`) |
| task_010 reverse_words | 3/3 | fail |
| task_011 cross-file | 3/3 | fail (would need to look across files) |
| task_012 trap test | 3/3 | fail (semantic exception handling) |
| task_013 config bug | 3/3 | fail (bug is in conftest, not in source) |
| **Total** | **39/39 = 100%** | **5/13 = 38.5%** |

Delta: **+8 tasks (+61.5 pp)** over the baseline. The three tasks designed to resist naive mutation (011 cross-file, 012 trap test, 013 config bug) all passed 3/3 under the LLM loop.

## Two self-caught classifier bugs (the real finding)

Both bugs were in classifiers I wrote this morning.

### Bug 1 — sanctioned test edits flagged as tampering

`classify_root_cause` flagged ANY edit under `tests/` as `test_tampering`, regardless of whether the task's `reference` specifically allowed it. On this run:

- **task_012** (the trap-test task): the reference whitelists `tests/test_version.py` because the fix *requires* updating the buggy test. All 3 seeds correctly edited it and the source. Original classifier: 3× `test_tampering`.
- **task_013** (the config-bug task): the reference whitelists `tests/conftest.py` because that *is* the fix. Original classifier: 3× `test_tampering`.

Fix: `classify_root_cause` now accepts the task's `reference` and checks `tests_modified` against it. A test edit counts as tampering only when the file is not in `reference.files` (or when `forbid_test_edits: true` is set). After the fix: 0 tampering across the run. See `_is_tampering` in `evals/failure_taxonomy.py`.

### Bug 2 — `must_contain` was AND-only, failing semantically-equivalent fixes

The reference for task_007 required `.lower()`; one seed used `.casefold()` (which is Python's *stronger* case folding, correct for Unicode). The reference for task_010 required both `reversed` AND `[::-1]` — a fix needs only one. Before the fix: 4× `fix_match=partial` false positives.

Fix: `must_contain` entries can now be either a string (required) or a nested list (alternatives, one-of). Example:

```json
"must_contain": [[".lower()", ".casefold()"]]
```

After the fix: 39/39 `fix_match=correct`. See `classify_against_reference` in `evals/failure_taxonomy.py`.

Both bugs were caught by looking at the raw labels *against* expected outcomes and asking why they disagreed. That is the diagnostic value the taxonomy was supposed to provide. In this run it provided it against itself.

## Threshold sensitivity

With 0 failures in the 39 runs, the ±1 perturbations of `TOOL_FAILURE_MIN_FAILED_CALLS` and `CONTEXT_MIN_TOOL_CALLS` leave the labels unchanged — stability 100% on all four perturbations.

This is **uninformative on this dataset**: the probe only tells you something when there are failures to classify. A real sensitivity number needs a run that produces failures (an ablation with `--no-planner` or `--preset weak-agent` would).

### Third classifier bug, caught by self-review

`threshold_sensitivity` was invoked without the per-task references, so its internal baseline reclassified the 6 sanctioned-test-edit runs (task_012 and task_013) as `test_tampering`. Its `base_label_distribution` field said `{clean_pass: 33, test_tampering: 6}` while the authoritative `classify_root_cause` (which did receive the references) returned `{clean_pass: 39}`. The reported stability of 100% was technically correct — threshold shifts don't change the tampering label, which depends on `_is_tampering` — but the base was silently inconsistent.

Fix: `threshold_sensitivity(results, tasks_by_id)` now threads the reference through to every reclassification call, and `tests/test_threshold_sensitivity.py::test_threshold_sensitivity_respects_reference_whitelist` locks the invariant. After the fix: `{clean_pass: 39}`.

## What the numbers do and don't say

- **39/39 clean is not a model claim** — it is a statement about `claude-sonnet-4-6` on 13 small bugfix workspaces under this specific harness, with the specific issue text I wrote. Any of those three can move the number.
- **N=3 is anecdote scale.** Two tasks out of three means "two times out of three", not "0.67 reliability". Published reliability numbers want N ≥ 10 and ideally varied inputs, not just varied sampling seeds.
- **Only model sampling is varied here.** Same issue text, same bug, same source each seed. A real benchmark would vary the input distribution (paraphrases, bug variants, decoy files).
- **13 of 13 tasks are hand-crafted by one person.** The `source` field records this. For publishable work, draw from an external benchmark (SWE-bench-mini, documented bugfix commits). See [next_steps.md](next_steps.md).
- **The sandbox was local**, not Docker, on this run (the eval was launched before the Docker-default flip). Numbers reported in future runs with Docker-default may differ.

## What this round added to the lab

Thirteen fixes from the self-review in this session. Highlights:

- **Harness no longer silently mutates workspaces**; `run_task` always copies to tmp.
- **Filename resolver** (symbol-aware) attaches corrected paths before the model sees the issue. Removed most of the task_004 friction pattern.
- **Shell-metachar rejection** in `_sanitize_command` with an actionable error ("drop the `2>&1`, there is no shell").
- **Test-tampering guard**: any edit to a test file is tracked separately and flagged unless the task sanctions it.
- **Reference-based fix_match** compares the final file contents (not just diff `+` lines) against `must_contain` / `must_not_contain`, with OR-semantics for alternatives.
- **Ablation flags** (`--no-planner`, `--no-resolver`, `--no-inspect-error`, `--preset weak-agent`) and a weak-agent preset for a fair LLM-level baseline.
- **`AgentMemory` split** into `summary()` / `diagnostics()` / `raw()` views.
- **Threshold sensitivity probe** on the root-cause classifier.
- **Docker-default sandbox** with `--local` opt-out.
- **95 framework tests** verdi (from 40 at the start of the day).

See the full punch list in [next_steps.md](next_steps.md).

## Reproduce

The numbers in this report come from a **local** sandbox run (`--local`). The current default is Docker; without `--local`, the eval runs inside a container and numbers may differ slightly (first run pays the ~30s image build, pytest is the same, but the Python minor version is pinned at 3.11 in the image).

```bash
export ANTHROPIC_API_KEY=...
cd mini-codex-reliability-lab

# Reproduces the v2 report exactly (local sandbox, 3 seeds):
python3 -m evals.run_eval --local --repeats 3 --temperature 0.2 --output reports/run.json

# Fresh baseline with reference-aware fix_match on each mutation:
python3 -m evals.run_baseline --output reports/baseline.json

# Preferred for publishable numbers (Docker, same command minus --local):
python3 -m evals.run_eval --repeats 3 --temperature 0.2 --output reports/run.json
```

Raw artifacts from this run: `run_3seeds.json` (raw), `run_3seeds_refined.json` (reclassified with the fixed taxonomy), `run_3seeds_root_cause_refined.json`, `run_3seeds_sensitivity.json`, `baseline_after_ast.json`, `run_3seeds.log`.
