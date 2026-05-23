# Failure Category Notes

These labels come from `evals/failure_taxonomy.py`. They are just rough buckets
for triage, not something I would treat as ground truth.

- `wrong_file_selected`: the agent went after the wrong file or function first.
  Check the early `search_code` and `read_file` calls.
- `bad_patch`: the edit applied, but the logic was still wrong. Start with the
  patch diff and the next failing test.
- `test_hallucination`: something looked green mid-run but the final full check
  still failed. Usually this means the agent ran too narrow a test target or
  misread truncated output.
- `syntax_error_in_patch`: a patch broke Python syntax. Look at the last edit
  that applied cleanly and then the traceback.
- `missed_edge_case`: the main path was fixed, but one or two edge-case tests
  stayed red.
- `over_editing`: the fix wandered into code that was not part of the bug.
- `insufficient_context`: the loop guessed early and barely read the workspace.
- `timeout`: it spent too many iterations circling the same area.
- `no_patch_applied`: nothing useful ever landed on disk.
- `unknown`: catch-all when the heuristics do not match anything obvious.

If a run matters, read the trace. The label is only there to help sort a pile of
failures quickly.
