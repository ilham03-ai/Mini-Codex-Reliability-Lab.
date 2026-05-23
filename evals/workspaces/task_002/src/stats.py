def find_max(arr: list) -> float:
    """Return the maximum value in arr."""
    if not arr:
        raise ValueError("Cannot find max of empty list")
    current_max = arr[0]
    for i in range(1, len(arr)):
        if arr[i] < current_max:  # BUG: should be >
            current_max = arr[i]
    return current_max
