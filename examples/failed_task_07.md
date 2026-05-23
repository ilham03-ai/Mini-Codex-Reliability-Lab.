# task_007

This one is useful because the bug is simple and the failure is not.

The right fix is obvious:

```diff
- if item == target:
+ if item.lower() == target.lower():
```

What actually happened in the bad run was messier. The agent first lowercased
only `target`, then only `item`, then tried to apply the fully correct patch
against stale file contents. That edit never matched, so it was skipped, and
the loop kept poking at the same line until it timed out.

The lesson from this task is mostly about state tracking. If the agent does not
re-read the file after a failed test, tiny edits can drift out of sync with what
is actually on disk.
