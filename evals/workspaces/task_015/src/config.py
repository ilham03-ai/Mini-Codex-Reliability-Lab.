import copy


def override_config(base: dict, overrides: dict) -> dict:
    """Return a new config built from `base` with `overrides` layered on top.

    The intent: callers can keep using `base` afterwards; the returned dict
    is a standalone object. Nested dicts in `base` should be independent
    from nested dicts in the returned value.

    Pattern this bug is modeled on: the classic "shallow copy where deep
    copy is needed". Documented in the stdlib `copy` module reference. The
    current implementation uses copy.copy, so nested dicts remain shared.
    """
    merged = copy.copy(base)  # BUG: shallow; nested dicts stay shared
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k].update(v)
        else:
            merged[k] = v
    return merged
