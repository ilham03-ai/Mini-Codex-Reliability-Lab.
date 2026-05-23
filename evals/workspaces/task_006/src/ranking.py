def top_k(scores: list, k: int) -> list:
    """Return the k highest scores in descending order."""
    sorted_scores = sorted(scores)  # BUG: should be sorted(scores, reverse=True)
    return sorted_scores[:k]
