def flatten(nested: list) -> list:
    """Flatten one level of nesting in a list of lists."""
    result = []
    for item in nested:
        if isinstance(item, list):
            result.extend(item)
        else:
            result.append(item)
    return result


def count_occurrences(items: list, target) -> int:
    """Count how many times target appears in items (case-insensitive for strings)."""
    count = 0
    for item in items:
        if isinstance(item, str) and isinstance(target, str):
            if item == target:  # BUG: should use .lower() for case-insensitive comparison
                count += 1
        elif item == target:
            count += 1
    return count
