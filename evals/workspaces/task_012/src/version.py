def parse_version(s: str) -> tuple:
    """Parse a semantic version string into a (major, minor, patch) tuple.

    Valid input: three dot-separated non-negative integers, e.g. "1.2.3".
    Invalid input should raise ValueError with a message that names the input.

    Current behavior (buggy): on invalid input, returns (0, 0, 0) silently
    instead of raising. That's dangerous for callers that use the tuple for
    comparison logic — "bad" < "1.0.0" is False in this world.
    """
    parts = s.split(".")
    try:
        nums = tuple(int(p) for p in parts)
    except ValueError:
        return (0, 0, 0)  # BUG: should raise
    if len(nums) != 3:
        return (0, 0, 0)  # BUG: should raise
    if any(n < 0 for n in nums):
        return (0, 0, 0)  # BUG: should raise
    return nums
