# task_001

Easy one. The loop found `binary_search` right away, read `src/search.py`, and
spotted that the success case returned `mid - 1` instead of `mid`.

The actual patch was one line:

```diff
- return mid - 1  # BUG: should return mid
+ return mid
```

After that, `python -m pytest tests/ -v` went green.

Useful mostly as a sanity check: if the agent struggles here, something is wrong
with the tool loop or the sandbox, not the task.
