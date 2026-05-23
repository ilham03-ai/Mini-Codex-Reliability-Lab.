def mean(data: list) -> float:
    if not data:
        raise ValueError("Cannot compute mean of empty list")
    return sum(data) / len(data)


def sample_variance(data: list) -> float:
    """Compute the sample variance (Bessel's correction: divide by N-1)."""
    if len(data) < 2:
        raise ValueError("Sample variance requires at least 2 data points")
    m = mean(data)
    squared_diffs = [(x - m) ** 2 for x in data]
    return sum(squared_diffs) / len(data)  # BUG: should divide by len(data) - 1


def sample_std(data: list) -> float:
    return sample_variance(data) ** 0.5
