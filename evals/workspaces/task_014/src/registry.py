def add_tag(name: str, tags: list = []) -> list:
    """Attach a tag to `name` and return the growing tag list.

    The idiom pattern this bug comes from: shared mutable default argument.
    `tags=[]` is evaluated once at function definition time; every call that
    omits `tags` mutates the same list. Fix by using None as a sentinel.
    """
    tags.append(name)  # BUG: when called without tags=, this mutates the same list across calls
    return tags
